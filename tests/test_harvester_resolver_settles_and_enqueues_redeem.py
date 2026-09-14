# Created: 2026-06-03
# Last reused or audited: 2026-09-08
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
import time
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from src.state.db import init_schema
from src.state.schema.payout_observations_schema import ensure_table as ensure_payout_table
from src.state.snapshot_repo import init_snapshot_schema


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


def _insert_payout(
    conn,
    *,
    condition_id,
    outcome_index,
    numerator,
    denominator=1,
    state=None,
    source="chain_rpc_finalized_v1",
    block_number=100,
    block_hash="0xabc",
):
    conn.execute(
        """INSERT INTO payout_observations (
               condition_id, outcome_index, payout_numerator,
               payout_denominator, state, block_number, block_hash,
               observed_at, source
           ) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-08-13T07:10:00+00:00', ?)""",
        (
            condition_id,
            outcome_index,
            numerator,
            denominator,
            state or ("RESOLVED_ZERO" if numerator == 0 else "RESOLVED_NONZERO"),
            block_number,
            block_hash,
            source,
        ),
    )


def test_finalized_payout_rows_bind_tokens_and_allow_independent_blocks(trade_conn):
    from src.execution import harvester_pnl_resolver as resolver

    ensure_payout_table(trade_conn)
    init_snapshot_schema(trade_conn, include_latest=False)
    condition_id = "0x" + "e" * 64
    portfolio, position = _winning_position(
        trade_id="chain-finalized-no",
        city="NYC",
        target_date="2026-08-12",
    )
    position.condition_id = condition_id
    position.token_id = "yes-token"
    position.no_token_id = "no-token"
    position.temperature_metric = "high"
    trade_conn.execute(
        """INSERT INTO executable_market_snapshots (
               snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
               question_id, yes_token_id, no_token_id, enable_orderbook, active,
               closed, min_tick_size, min_order_size, fee_details_json,
               token_map_json, neg_risk, orderbook_top_bid, orderbook_top_ask,
               orderbook_depth_json, raw_gamma_payload_hash,
               raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
               captured_at, freshness_deadline
           ) VALUES (
               'snap-finalized', 'gamma', 'event', 'nyc-aug-12', ?, 'question',
               'yes-token', 'no-token', 1, 0, 1, '0.001', '1', '{}',
               '{"YES":"yes-token","NO":"no-token"}', 0, '0', '0', '{}',
               'g', 'c', 'b', 'CHAIN', '2026-08-13T07:20:00+00:00',
               '2026-08-13T07:21:00+00:00'
           )""",
        (condition_id,),
    )
    _insert_payout(
        trade_conn,
        condition_id=condition_id,
        outcome_index=0,
        numerator=0,
        block_number=101,
        block_hash="0x101",
    )
    _insert_payout(
        trade_conn,
        condition_id=condition_id,
        outcome_index=1,
        numerator=1,
        block_number=100,
        block_hash="0x100",
    )

    snapshots = resolver._condition_market_snapshots(trade_conn, {condition_id})
    rows = resolver._read_finalized_payout_settlement_rows(
        trade_conn,
        portfolio,
        {("NYC", "2026-08-12", "high")},
        snapshots,
    )

    assert rows == [{
        "city": "NYC",
        "target_date": "2026-08-12",
        "market_slug": "nyc-aug-12",
        "winning_bin": None,
        "temperature_metric": "high",
        "authority": "VENUE_RESOLVED",
        "settlement_source": "polymarket_chain_rpc_finalized_v1",
        "settlement_value": None,
        "settlement_scope": "condition",
        "condition_id": condition_id,
        "condition_yes_won": False,
    }]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_slot",
        "unknown",
        "legacy_source",
        "unequal_denominator",
        "double_winner",
        "partial_payout",
        "token_mismatch",
    ],
)
def test_finalized_payout_reader_fails_closed_on_incomplete_authority(
    trade_conn, mutation
):
    from src.execution import harvester_pnl_resolver as resolver

    ensure_payout_table(trade_conn)
    init_snapshot_schema(trade_conn, include_latest=False)
    condition_id = "0x" + "f" * 64
    portfolio, position = _winning_position(
        trade_id=f"malformed-{mutation}", city="Dallas", target_date="2026-08-12"
    )
    position.condition_id = condition_id
    position.token_id = "yes-token"
    position.no_token_id = "no-token"
    position.temperature_metric = "high"
    snapshot_no = "wrong-no-token" if mutation == "token_mismatch" else "no-token"
    trade_conn.execute(
        """INSERT INTO executable_market_snapshots (
               snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
               question_id, yes_token_id, no_token_id, enable_orderbook, active,
               closed, min_tick_size, min_order_size, fee_details_json,
               token_map_json, neg_risk, orderbook_top_bid, orderbook_top_ask,
               orderbook_depth_json, raw_gamma_payload_hash,
               raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
               captured_at, freshness_deadline
           ) VALUES (
               ?, 'gamma', 'event', 'dallas-aug-12', ?, 'question',
               'yes-token', ?, 1, 0, 1, '0.001', '1', '{}', '{}', 0,
               '0', '0', '{}', 'g', 'c', 'b', 'CHAIN',
               '2026-08-13T07:20:00+00:00', '2026-08-13T07:21:00+00:00'
           )""",
        (f"snap-{mutation}", condition_id, snapshot_no),
    )
    if mutation != "missing_slot":
        _insert_payout(
            trade_conn,
            condition_id=condition_id,
            outcome_index=0,
            numerator=(
                None
                if mutation == "unknown"
                else (1 if mutation == "double_winner" else 0)
            ),
            denominator=(2 if mutation == "partial_payout" else 1),
            state=("UNKNOWN" if mutation == "unknown" else None),
            source=("chain_rpc" if mutation == "legacy_source" else "chain_rpc_finalized_v1"),
        )
    _insert_payout(
        trade_conn,
        condition_id=condition_id,
        outcome_index=1,
        numerator=1,
        denominator=(2 if mutation == "unequal_denominator" else 1),
    )

    snapshots = resolver._condition_market_snapshots(trade_conn, {condition_id})
    assert resolver._read_finalized_payout_settlement_rows(
        trade_conn,
        portfolio,
        {("Dallas", "2026-08-12", "high")},
        snapshots,
    ) == []


def test_finalized_payout_drains_when_forecast_read_fails_without_hiding_family_truth(
    trade_conn, monkeypatch
):
    """Economic payout drains independently; family truth still reaches siblings."""
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver

    ensure_payout_table(trade_conn)
    init_snapshot_schema(trade_conn, include_latest=False)
    payout_condition = "0x" + "1" * 64
    sibling_condition = "0x" + "2" * 64
    portfolio, payout_position = _winning_position(
        trade_id="payout-position", city="NYC", target_date="2026-08-12"
    )
    payout_position.condition_id = payout_condition
    payout_position.token_id = "payout-yes"
    payout_position.no_token_id = "payout-no"
    _, sibling_position = _winning_position(
        trade_id="sibling-position", city="NYC", target_date="2026-08-12"
    )
    sibling_position.condition_id = sibling_condition
    sibling_position.token_id = "sibling-yes"
    sibling_position.no_token_id = "sibling-no"
    portfolio.positions.append(sibling_position)
    trade_conn.execute(
        """INSERT INTO executable_market_snapshots (
               snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
               question_id, yes_token_id, no_token_id, enable_orderbook, active,
               closed, min_tick_size, min_order_size, fee_details_json,
               token_map_json, neg_risk, orderbook_top_bid, orderbook_top_ask,
               orderbook_depth_json, raw_gamma_payload_hash,
               raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
               captured_at, freshness_deadline
           ) VALUES (
               'snap-e2e', 'gamma', 'event', 'nyc-aug-12', ?, 'question',
               'payout-yes', 'payout-no', 1, 0, 1, '0.001', '1', '{}', '{}',
               0, '0', '0', '{}', 'g', 'c', 'b', 'CHAIN',
               '2026-08-13T07:20:00+00:00', '2026-08-13T07:21:00+00:00'
           )""",
        (payout_condition,),
    )
    _insert_payout(
        trade_conn,
        condition_id=payout_condition,
        outcome_index=0,
        numerator=0,
    )
    _insert_payout(
        trade_conn,
        condition_id=payout_condition,
        outcome_index=1,
        numerator=1,
    )
    trade_conn.commit()

    family_row = {
        "city": "NYC",
        "target_date": "2026-08-12",
        "market_slug": "nyc-aug-12",
        "winning_bin": "31°C",
        "temperature_metric": "high",
        "authority": "VENUE_RESOLVED",
        "settlement_source": "polymarket_gamma",
        "settlement_value": None,
    }
    forecasts_conn = MagicMock()
    forecasts_conn.execute.side_effect = sqlite3.OperationalError("forecast unavailable")
    monkeypatch.setattr(
        "src.state.portfolio.load_portfolio", lambda *args, **kwargs: portfolio
    )
    monkeypatch.setattr("src.state.portfolio.save_portfolio", lambda *a, **kw: None)
    monkeypatch.setattr(
        "src.state.strategy_tracker.get_tracker", lambda: MagicMock()
    )
    monkeypatch.setattr("src.state.strategy_tracker.save_tracker", lambda *a, **kw: None)
    monkeypatch.setattr(
        "src.state.canonical_write.commit_then_export",
        lambda conn, *, db_op, json_exports: db_op(),
    )
    monkeypatch.setattr(
        "src.state.decision_chain.store_settlement_records", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        resolver, "_read_venue_resolved_settlement_rows", lambda *a, **kw: [family_row]
    )
    calls = []

    def capture_settlement(*args, **kwargs):
        calls.append({
            "truth": kwargs["settlement_truth_source"],
            "condition_id": kwargs["settlement_condition_id"],
        })
        return 0

    monkeypatch.setattr(hv, "_settle_positions", capture_settlement)

    result = resolver.resolve_pnl_for_settled_markets(trade_conn, forecasts_conn)

    assert result["status"] == "ok"
    assert result["errors"] == 1
    assert calls == [
        {"truth": "trades.payout_observations", "condition_id": payout_condition},
        {"truth": "gamma_exact_held_event", "condition_id": ""},
    ]


def test_settlement_redecision_skips_position_changed_while_waiting_for_writer(
    trade_conn, forecasts_conn_with_verified_settlement, monkeypatch
):
    """Discovery never settles a position whose canonical version advanced."""
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver

    portfolio, position = _winning_position()
    trade_conn.execute(
        """INSERT INTO position_current (
               position_id, phase, city, target_date, temperature_metric, updated_at
           ) VALUES (?, 'active', ?, ?, 'high', ?)""",
        (position.trade_id, position.city, position.target_date, "before-writer"),
    )
    trade_conn.commit()
    monkeypatch.setattr(
        "src.state.portfolio.load_portfolio", lambda *args, **kwargs: portfolio
    )
    monkeypatch.setattr(
        resolver, "_read_venue_resolved_settlement_rows", lambda *a, **kw: []
    )
    monkeypatch.setattr(resolver, "_is_canonical_trade_connection", lambda _c: True)

    @contextmanager
    def position_changes_before_writer(_conn, *, canonical):
        assert canonical is True
        trade_conn.execute(
            """INSERT INTO position_events (
                   event_id, position_id, event_version, sequence_no, event_type,
                   occurred_at, phase_before, phase_after, source_module,
                   payload_json, caused_by, env
               ) VALUES (?, ?, 1, 1, 'MONITOR_REFRESHED', ?, 'active', 'active',
                         'tests.harvester_resolver', '{}', 'monitor_refresh', 'live')""",
            (
                "position-changed-before-writer",
                position.trade_id,
                "2026-08-13T14:50:00+00:00",
            ),
        )
        trade_conn.commit()
        trade_conn.execute("BEGIN IMMEDIATE")
        yield time.monotonic() + 5

    monkeypatch.setattr(
        resolver, "_settlement_writer_transaction", position_changes_before_writer
    )
    settle = MagicMock()
    monkeypatch.setattr(hv, "_settle_positions", settle)

    result = resolver.resolve_pnl_for_settled_markets(
        trade_conn, forecasts_conn_with_verified_settlement
    )

    assert result["status"] == "awaiting_truth_writer"
    assert result["positions_settled"] == 0
    settle.assert_not_called()


def test_settlement_redecision_skips_family_when_canonical_sibling_is_not_hydrated(
    trade_conn, forecasts_conn_with_verified_settlement, monkeypatch
):
    """A partial portfolio snapshot can never authorize a family settlement."""
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver

    portfolio, position = _winning_position()
    for position_id, condition_id in (
        (position.trade_id, position.condition_id),
        ("canonical-sibling", "canonical-sibling-condition"),
    ):
        trade_conn.execute(
            """INSERT INTO position_current (
                   position_id, phase, city, target_date, temperature_metric,
                   condition_id, updated_at
               ) VALUES (?, 'active', ?, ?, 'high', ?, ?)""",
            (
                position_id,
                position.city,
                position.target_date,
                condition_id,
                "before-writer",
            ),
        )
    trade_conn.commit()
    monkeypatch.setattr(
        "src.state.portfolio.load_portfolio", lambda *args, **kwargs: portfolio
    )
    monkeypatch.setattr(
        resolver, "_read_venue_resolved_settlement_rows", lambda *a, **kw: []
    )
    monkeypatch.setattr(resolver, "_is_canonical_trade_connection", lambda _c: True)

    @contextmanager
    def writer(_conn, *, canonical):
        assert canonical is True
        trade_conn.execute("BEGIN IMMEDIATE")
        yield time.monotonic() + 5

    monkeypatch.setattr(resolver, "_settlement_writer_transaction", writer)
    settle = MagicMock()
    monkeypatch.setattr(hv, "_settle_positions", settle)

    result = resolver.resolve_pnl_for_settled_markets(
        trade_conn, forecasts_conn_with_verified_settlement
    )

    assert result["status"] == "awaiting_truth_writer"
    settle.assert_not_called()


def test_settlement_writer_expired_admission_releases_lease(
    trade_conn, monkeypatch
):
    from src.execution import harvester_pnl_resolver as resolver
    from src.state import write_coordinator

    events = []

    @contextmanager
    def lease(*_args, **_kwargs):
        events.append("enter")
        try:
            yield type("Lease", (), {"acquired_at": time.monotonic() - 10})()
        finally:
            events.append("exit")

    coordinator = MagicMock()
    coordinator.lease.side_effect = lease
    monkeypatch.setattr(
        write_coordinator, "default_runtime_write_coordinator", lambda: coordinator
    )

    with pytest.raises(resolver._SettlementWriterDeadlineExceeded):
        with resolver._settlement_writer_transaction(trade_conn, canonical=True):
            pytest.fail("expired lease must not begin a SQLite transaction")

    assert events == ["enter", "exit"]
    assert not trade_conn.in_transaction


def test_settlement_writer_busy_fails_fast_and_releases_lease(
    tmp_path, monkeypatch
):
    """A non-cooperating SQLite writer cannot wedge the harvester for minutes."""
    from src.execution import harvester_pnl_resolver as resolver
    from src.state import write_coordinator

    db_path = tmp_path / "trade-lock.db"
    holder = sqlite3.connect(db_path)
    contender = sqlite3.connect(db_path, timeout=30)
    holder.execute("CREATE TABLE facts (value TEXT)")
    holder.commit()
    holder.execute("BEGIN IMMEDIATE")

    lease_events = []

    @contextmanager
    def lease(*_args, **_kwargs):
        lease_events.append("enter")
        try:
            yield type("Lease", (), {"acquired_at": time.monotonic()})()
        finally:
            lease_events.append("exit")

    coordinator = MagicMock()
    coordinator.lease.side_effect = lease
    monkeypatch.setattr(resolver, "_is_canonical_trade_connection", lambda _c: True)
    monkeypatch.setattr(
        write_coordinator, "default_runtime_write_coordinator", lambda: coordinator
    )

    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        with resolver._settlement_writer_transaction(contender, canonical=True):
            pytest.fail("writer transaction must not open behind a foreign lock")
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert lease_events == ["enter", "exit"]
    assert contender.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
    assert not contender.in_transaction
    holder.rollback()
    holder.close()
    contender.close()


def test_settlement_writer_commits_before_releasing_lease_and_export(
    trade_conn, forecasts_conn_with_verified_settlement, monkeypatch
):
    """The lease covers commit, while derived JSON runs only after release."""
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver

    portfolio, _position = _winning_position()
    monkeypatch.setattr(
        "src.state.portfolio.load_portfolio", lambda *args, **kwargs: portfolio
    )
    monkeypatch.setattr(
        resolver, "_read_venue_resolved_settlement_rows", lambda *a, **kw: []
    )
    events = []

    @contextmanager
    def writer(_conn, *, canonical):
        assert canonical is False
        events.append("lease_enter")
        trade_conn.execute("BEGIN IMMEDIATE")
        try:
            yield None
        finally:
            events.append("lease_exit")

    monkeypatch.setattr(resolver, "_settlement_writer_transaction", writer)
    monkeypatch.setattr(hv, "_settle_positions", lambda *a, **kw: 1)
    monkeypatch.setattr(
        "src.state.decision_chain.store_settlement_records", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "src.state.portfolio.save_portfolio",
        lambda *a, **kw: events.append("portfolio_export"),
    )
    monkeypatch.setattr(
        "src.state.strategy_tracker.get_tracker", lambda: MagicMock()
    )
    monkeypatch.setattr(
        "src.state.strategy_tracker.save_tracker",
        lambda *a, **kw: events.append("tracker_export"),
    )

    result = resolver.resolve_pnl_for_settled_markets(
        trade_conn, forecasts_conn_with_verified_settlement
    )

    assert result["positions_settled"] == 1
    assert events == [
        "lease_enter",
        "lease_exit",
        "portfolio_export",
        "tracker_export",
    ]
    assert not trade_conn.in_transaction


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

    def _mark_settled(
        _portfolio,
        trade_id,
        settlement_price,
        reason,
        *,
        audit_conn=None,
    ):
        assert audit_conn is trade_conn
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


@pytest.mark.parametrize("metric", ["high", "low"])
def test_resolver_hydrates_closed_siblings_with_pending_exit_dust(
    trade_conn, forecasts_conn_with_verified_settlement, monkeypatch, metric
):
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver
    from src.state.portfolio import compute_settlement_close

    forecasts_conn_with_verified_settlement.execute(
        "UPDATE settlement_outcomes SET temperature_metric=?", (metric,)
    )
    forecasts_conn_with_verified_settlement.commit()
    for position_id, phase in (("dust", "pending_exit"), ("closed", "economically_closed"), ("terminal", "settled")):
        trade_conn.execute(
            """INSERT INTO position_current (
                position_id, trade_id, phase, market_id, city, cluster, target_date,
                bin_label, direction, unit, shares, size_usd, cost_basis_usd,
                entry_price, p_posterior, strategy_key, chain_state,
                temperature_metric, updated_at, exit_price, realized_pnl_usd
            ) VALUES (?, ?, ?, 'market', 'Shanghai', 'Asia', '2026-05-29',
                      '27-28°C', 'buy_yes', 'C', 0.0027, 0.00135, 0.00135,
                      0.5, 0.7, 'center_buy', 'synced', ?, 'before-writer', 0.27, -6.16)""",
            (position_id, position_id, phase, metric),
        )
    trade_conn.commit()
    monkeypatch.setattr(resolver, "_is_canonical_trade_connection", lambda _c: True)
    monkeypatch.setattr(resolver, "_read_venue_resolved_settlement_rows", lambda *a, **kw: [])
    monkeypatch.setattr("src.state.strategy_tracker.get_tracker", lambda: MagicMock())
    monkeypatch.setattr("src.state.strategy_tracker.save_tracker", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.portfolio.save_portfolio", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.decision_chain.store_settlement_records", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.canonical_write.commit_then_export", lambda conn, *, db_op, json_exports: db_op())

    @contextmanager
    def writer(conn, *, canonical):
        assert conn is trade_conn and canonical
        conn.execute("BEGIN IMMEDIATE")
        yield time.monotonic() + 5

    monkeypatch.setattr(resolver, "_settlement_writer_transaction", writer)
    closed_positions = []

    def settle(conn, portfolio, *args, **kwargs):
        assert conn is trade_conn
        assert portfolio.authority_scope == "settlement_cohort"
        assert {p.trade_id for p in portfolio.positions} == {"dust", "closed"}
        for pos in list(portfolio.positions):
            closed_positions.append(compute_settlement_close(portfolio, pos.trade_id, 1.0, "SETTLEMENT"))
        return len(closed_positions)

    monkeypatch.setattr(hv, "_settle_positions", settle)
    result = resolver.resolve_pnl_for_settled_markets(trade_conn, forecasts_conn_with_verified_settlement)
    assert result["positions_settled"] == 2
    booked = next(p for p in closed_positions if p.trade_id == "closed")
    assert booked.pnl == pytest.approx(-6.16)
    assert booked.exit_price == pytest.approx(0.27)
    assert all(p.state == "settled" for p in closed_positions)


def test_resolver_empty_keys_never_loads_unbounded_settlement_cohort(trade_conn, monkeypatch):
    from src.execution import harvester_pnl_resolver as resolver
    from src.state.portfolio import PortfolioState
    calls = []

    def load(**kwargs):
        calls.append(kwargs)
        assert kwargs == {"connection": trade_conn, "open_positions_only": True}
        return PortfolioState(positions=[])

    monkeypatch.setattr("src.state.portfolio.load_portfolio", load)
    result = resolver.resolve_pnl_for_settled_markets(trade_conn, MagicMock())
    assert result["status"] == "awaiting_truth_writer"
    assert result["open_position_keys_checked"] == 0
    assert len(calls) == 1


def test_resolver_refuses_to_settle_degraded_cohort_hydration(
    trade_conn, forecasts_conn_with_verified_settlement, monkeypatch
):
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver
    from src.state.portfolio import PortfolioState

    portfolio, _position = _winning_position()
    degraded = PortfolioState(
        positions=[], portfolio_loader_degraded=True, authority="degraded"
    )
    calls = iter((portfolio, degraded))
    monkeypatch.setattr(
        "src.state.portfolio.load_portfolio", lambda *args, **kwargs: next(calls)
    )
    monkeypatch.setattr(
        hv, "_settle_positions",
        lambda *args, **kwargs: pytest.fail("degraded cohort must not settle"),
    )
    with pytest.raises(RuntimeError, match="SETTLEMENT_COHORT_NOT_AUTHORITATIVE"):
        resolver.resolve_pnl_for_settled_markets(
            trade_conn, forecasts_conn_with_verified_settlement
        )


def _empty_settlement_outcomes_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT, target_date TEXT, market_slug TEXT, winning_bin TEXT,
            temperature_metric TEXT, authority TEXT, settlement_source TEXT,
            settlement_value REAL, settled_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def test_in_lease_reverify_narrows_to_empty_when_no_row_survives_into_the_lease(
    trade_conn, monkeypatch
):
    """No discovered row makes it into the lease -> the in-lease re-verify
    fingerprints no keys, instead of every open settlement key."""
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver

    init_snapshot_schema(trade_conn, include_latest=False)
    portfolio, position = _winning_position()
    trade_conn.execute(
        """INSERT INTO position_current (
               position_id, phase, city, target_date, temperature_metric, updated_at
           ) VALUES (?, 'active', ?, ?, 'high', ?)""",
        (position.trade_id, position.city, position.target_date, "before-writer"),
    )
    trade_conn.commit()
    forecasts_conn = _empty_settlement_outcomes_conn()

    monkeypatch.setattr("src.state.portfolio.load_portfolio", lambda *a, **kw: portfolio)
    monkeypatch.setattr(resolver, "_read_venue_resolved_settlement_rows", lambda *a, **kw: [])
    monkeypatch.setattr(resolver, "_is_canonical_trade_connection", lambda _c: True)

    # Pre-lease read finds one finalized payout (so the outer check enters the
    # lease); the in-lease re-read of that same query comes back empty, as if
    # the observation raced out from under it.
    payout_calls = {"n": 0}

    def payout_stub(_conn, _portfolio, _keys, _snapshots):
        payout_calls["n"] += 1
        if payout_calls["n"] == 1:
            return [{
                "city": position.city,
                "target_date": position.target_date,
                "market_slug": "slug",
                "winning_bin": None,
                "temperature_metric": "high",
                "authority": "VENUE_RESOLVED",
                "settlement_source": "polymarket_chain_rpc_finalized_v1",
                "settlement_value": None,
                "settlement_scope": "condition",
                "condition_id": "0x" + "a" * 40,
                "condition_yes_won": True,
            }]
        return []

    monkeypatch.setattr(resolver, "_read_finalized_payout_settlement_rows", payout_stub)

    @contextmanager
    def writer(_conn, *, canonical):
        assert canonical is True
        trade_conn.execute("BEGIN IMMEDIATE")
        yield time.monotonic() + 5

    monkeypatch.setattr(resolver, "_settlement_writer_transaction", writer)

    version_calls = []
    original_versions = resolver._canonical_position_versions

    def spy_versions(conn, keys):
        keys = set(keys)
        version_calls.append(keys)
        return original_versions(conn, keys)

    monkeypatch.setattr(resolver, "_canonical_position_versions", spy_versions)
    settle = MagicMock()
    monkeypatch.setattr(hv, "_settle_positions", settle)

    result = resolver.resolve_pnl_for_settled_markets(trade_conn, forecasts_conn)

    assert result["status"] == "awaiting_truth_writer"
    assert result["positions_settled"] == 0
    settle.assert_not_called()
    assert payout_calls["n"] == 2
    # First call is the pre-lease snapshot over every open settlement key; the
    # second is the in-lease re-verify, narrowed to what the applied rows
    # reference -- here, nothing.
    assert len(version_calls) == 2
    assert version_calls[0] == {(position.city, position.target_date, "high")}
    assert version_calls[1] == set()


def test_in_lease_reverify_narrows_to_rows_referenced_keys(trade_conn, monkeypatch):
    """The in-lease re-verify re-fingerprints only the keys the discovered rows
    touch, and a fingerprint change on one of those keys still stales that row
    even though the unrelated open keys are no longer re-scanned."""
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver

    init_snapshot_schema(trade_conn, include_latest=False)
    portfolio, pos_a = _winning_position(
        trade_id="fam-a", city="CityA", target_date="2026-06-01"
    )
    _, pos_b = _winning_position(trade_id="fam-b", city="CityB", target_date="2026-06-01")
    _, pos_c = _winning_position(trade_id="fam-c", city="CityC", target_date="2026-06-01")
    _, pos_d = _winning_position(trade_id="fam-d", city="CityD", target_date="2026-06-01")
    portfolio.positions = [pos_a, pos_b, pos_c, pos_d]

    for pos in (pos_a, pos_b, pos_c, pos_d):
        trade_conn.execute(
            """INSERT INTO position_current (
                   position_id, phase, city, target_date, temperature_metric, updated_at
               ) VALUES (?, 'active', ?, ?, 'high', ?)""",
            (pos.trade_id, pos.city, pos.target_date, "before-writer"),
        )
    trade_conn.commit()

    forecasts_conn = _empty_settlement_outcomes_conn()
    for pos in (pos_a, pos_b):
        forecasts_conn.execute(
            "INSERT INTO settlement_outcomes "
            "(city, target_date, market_slug, winning_bin, temperature_metric, authority, "
            " settlement_source, settlement_value, settled_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                pos.city, pos.target_date, f"{pos.city}-slug", pos.bin_label, "high",
                "VERIFIED", "wu_icao", 27.0, "2026-06-03T18:46:00Z",
            ),
        )
    forecasts_conn.commit()

    monkeypatch.setattr("src.state.portfolio.load_portfolio", lambda *a, **kw: portfolio)
    monkeypatch.setattr(resolver, "_read_venue_resolved_settlement_rows", lambda *a, **kw: [])
    monkeypatch.setattr(resolver, "_read_finalized_payout_settlement_rows", lambda *a, **kw: [])
    monkeypatch.setattr(resolver, "_is_canonical_trade_connection", lambda _c: True)

    @contextmanager
    def writer(_conn, *, canonical):
        assert canonical is True
        # Concurrent mutation of CityA's position between the pre-lease
        # snapshot and the in-lease re-verify: only its fingerprint moves.
        trade_conn.execute(
            """INSERT INTO position_events (
                   event_id, position_id, event_version, sequence_no, event_type,
                   occurred_at, phase_before, phase_after, source_module,
                   payload_json, caused_by, env
               ) VALUES (?, ?, 1, 1, 'MONITOR_REFRESHED', ?, 'active', 'active',
                         'tests.harvester_resolver', '{}', 'monitor_refresh', 'live')""",
            ("fam-a-changed", pos_a.trade_id, "2026-08-13T14:50:00+00:00"),
        )
        trade_conn.commit()
        trade_conn.execute("BEGIN IMMEDIATE")
        yield time.monotonic() + 5

    monkeypatch.setattr(resolver, "_settlement_writer_transaction", writer)

    version_calls = []
    original_versions = resolver._canonical_position_versions

    def spy_versions(conn, keys):
        keys = set(keys)
        version_calls.append(keys)
        return original_versions(conn, keys)

    monkeypatch.setattr(resolver, "_canonical_position_versions", spy_versions)

    settled = []

    def settle(_conn, _portfolio, city_name, target_date, *_args, **_kwargs):
        settled.append((city_name, target_date))
        return 1

    monkeypatch.setattr(hv, "_settle_positions", settle)
    monkeypatch.setattr(
        "src.state.decision_chain.store_settlement_records", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "src.state.canonical_write.commit_then_export",
        lambda conn, *, db_op, json_exports: db_op(),
    )
    monkeypatch.setattr("src.state.portfolio.save_portfolio", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.strategy_tracker.get_tracker", lambda: MagicMock())
    monkeypatch.setattr("src.state.strategy_tracker.save_tracker", lambda *a, **kw: None)

    result = resolver.resolve_pnl_for_settled_markets(trade_conn, forecasts_conn)

    assert len(version_calls) == 2
    all_keys = {
        ("CityA", "2026-06-01", "high"),
        ("CityB", "2026-06-01", "high"),
        ("CityC", "2026-06-01", "high"),
        ("CityD", "2026-06-01", "high"),
    }
    assert version_calls[0] == all_keys
    # Narrowed to only the two keys the VERIFIED rows reference -- CityC and
    # CityD are open but untouched by any discovered row.
    assert version_calls[1] == {
        ("CityA", "2026-06-01", "high"),
        ("CityB", "2026-06-01", "high"),
    }
    # CityA's fingerprint moved underneath the lease; its row must still be
    # rejected as stale even though the re-verify no longer scans CityC/CityD.
    assert settled == [("CityB", "2026-06-01")]
    assert result["positions_settled"] == 1


def test_market_snapshot_lookup_runs_once_before_the_write_lease(
    trade_conn, monkeypatch
):
    """Token-id/event-slug snapshot metadata is read once, before the lease
    is acquired, and reused for the in-lease payout re-read -- it is never
    re-queried under the bounded write budget."""
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver

    init_snapshot_schema(trade_conn, include_latest=False)
    portfolio, position = _winning_position()
    trade_conn.execute(
        """INSERT INTO position_current (
               position_id, phase, city, target_date, temperature_metric, updated_at
           ) VALUES (?, 'active', ?, ?, 'high', ?)""",
        (position.trade_id, position.city, position.target_date, "before-writer"),
    )
    trade_conn.commit()
    forecasts_conn = _empty_settlement_outcomes_conn()

    monkeypatch.setattr("src.state.portfolio.load_portfolio", lambda *a, **kw: portfolio)
    monkeypatch.setattr(resolver, "_read_venue_resolved_settlement_rows", lambda *a, **kw: [])
    monkeypatch.setattr(resolver, "_is_canonical_trade_connection", lambda _c: True)

    events = []
    original_snapshots = resolver._condition_market_snapshots

    def spy_snapshots(conn, condition_ids):
        events.append("snapshot_lookup")
        return original_snapshots(conn, condition_ids)

    monkeypatch.setattr(resolver, "_condition_market_snapshots", spy_snapshots)

    # Pre-lease discovery finds one finalized payout (enters the lease); the
    # in-lease re-read of the same payout query comes back empty. The
    # snapshot lookup itself must not run a second time for this re-read.
    payout_calls = {"n": 0}

    def payout_stub(_conn, _portfolio, _keys, _snapshots):
        payout_calls["n"] += 1
        if payout_calls["n"] == 1:
            return [{
                "city": position.city,
                "target_date": position.target_date,
                "market_slug": "slug",
                "winning_bin": None,
                "temperature_metric": "high",
                "authority": "VENUE_RESOLVED",
                "settlement_source": "polymarket_chain_rpc_finalized_v1",
                "settlement_value": None,
                "settlement_scope": "condition",
                "condition_id": "0x" + "a" * 40,
                "condition_yes_won": True,
            }]
        return []

    monkeypatch.setattr(resolver, "_read_finalized_payout_settlement_rows", payout_stub)

    @contextmanager
    def writer(_conn, *, canonical):
        assert canonical is True
        events.append("lease_enter")
        trade_conn.execute("BEGIN IMMEDIATE")
        try:
            yield time.monotonic() + 5
        finally:
            events.append("lease_exit")

    monkeypatch.setattr(resolver, "_settlement_writer_transaction", writer)
    settle = MagicMock()
    monkeypatch.setattr(hv, "_settle_positions", settle)

    resolver.resolve_pnl_for_settled_markets(trade_conn, forecasts_conn)

    # Snapshot lookup happens exactly once, strictly before the lease opens.
    assert events == ["snapshot_lookup", "lease_enter", "lease_exit"]
    # The payout observations read is still repeated inside the lease --
    # only the snapshot lookup was hoisted out, not the truth re-read itself.
    assert payout_calls["n"] == 2


def test_condition_ids_for_keys_matches_payout_reader_grouping(monkeypatch):
    """`_condition_ids_for_keys` and `_read_finalized_payout_settlement_rows`
    must never silently diverge on which positions a key set admits -- both
    now delegate to the one shared filter, `_positions_by_condition_for_keys`."""
    from src.execution import harvester_pnl_resolver as resolver

    _, pos_a_yes = _winning_position(trade_id="a-yes", city="CityA", target_date="2026-06-01")
    _, pos_a_no = _winning_position(trade_id="a-no", city="CityA", target_date="2026-06-01")
    pos_a_yes.condition_id = pos_a_no.condition_id = "cond-a"
    _, pos_b = _winning_position(trade_id="b", city="CityB", target_date="2026-06-01")
    pos_b.condition_id = "cond-b"
    # Open key, but no condition_id -- must be excluded by both callers.
    _, pos_c = _winning_position(trade_id="c", city="CityC", target_date="2026-06-01")
    pos_c.condition_id = ""
    # Has a condition_id, but its key is not in the admitted set -- excluded.
    _, pos_d = _winning_position(trade_id="d", city="CityD", target_date="2026-06-01")
    pos_d.condition_id = "cond-d"

    portfolio = MagicMock()
    portfolio.positions = [pos_a_yes, pos_a_no, pos_b, pos_c, pos_d]
    keys = {
        ("CityA", "2026-06-01", "high"),
        ("CityB", "2026-06-01", "high"),
        ("CityC", "2026-06-01", "high"),
    }

    grouped = resolver._positions_by_condition_for_keys(portfolio, keys)
    condition_ids = resolver._condition_ids_for_keys(portfolio, keys)

    assert grouped == {
        "cond-a": [pos_a_yes, pos_a_no],
        "cond-b": [pos_b],
    }
    assert condition_ids == set(grouped) == {"cond-a", "cond-b"}


def test_in_lease_payout_reread_scoped_to_empty_when_no_pre_lease_candidate(
    trade_conn, monkeypatch
):
    """Two open, unverified keys; neither has a finalized payout pre-lease, but
    a VERIFIED row on a third key still takes the lease. The in-lease payout
    re-read must be called with an empty key set -- and therefore issue no
    SQL -- rather than the parent code's `settlement_keys - verified_keys`."""
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver

    ensure_payout_table(trade_conn)
    init_snapshot_schema(trade_conn, include_latest=False)
    portfolio, pos_verified = _winning_position(
        trade_id="ver-a", city="CityA", target_date="2026-08-12"
    )
    _, pos_unverified = _winning_position(
        trade_id="unver-b", city="CityB", target_date="2026-08-12"
    )
    portfolio.positions = [pos_verified, pos_unverified]

    for pos in (pos_verified, pos_unverified):
        trade_conn.execute(
            """INSERT INTO position_current (
                   position_id, phase, city, target_date, temperature_metric, updated_at
               ) VALUES (?, 'active', ?, ?, 'high', ?)""",
            (pos.trade_id, pos.city, pos.target_date, "before-writer"),
        )
    trade_conn.commit()

    forecasts_conn = _empty_settlement_outcomes_conn()
    forecasts_conn.execute(
        "INSERT INTO settlement_outcomes "
        "(city, target_date, market_slug, winning_bin, temperature_metric, authority, "
        " settlement_source, settlement_value, settled_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "CityA", "2026-08-12", "citya-slug", pos_verified.bin_label, "high",
            "VERIFIED", "wu_icao", 27.0, "2026-08-13T00:00:00Z",
        ),
    )
    forecasts_conn.commit()
    # CityB has no payout_observations rows at all -- the pre-lease payout
    # scan over {CityB} finds nothing.

    monkeypatch.setattr("src.state.portfolio.load_portfolio", lambda *a, **kw: portfolio)
    monkeypatch.setattr(resolver, "_read_venue_resolved_settlement_rows", lambda *a, **kw: [])
    monkeypatch.setattr(resolver, "_is_canonical_trade_connection", lambda _c: True)

    payout_calls: list[set] = []
    original_payout_reader = resolver._read_finalized_payout_settlement_rows

    def payout_spy(conn, portfolio_arg, keys, snapshots):
        keys = set(keys)
        payout_calls.append(keys)
        return original_payout_reader(conn, portfolio_arg, keys, snapshots)

    monkeypatch.setattr(resolver, "_read_finalized_payout_settlement_rows", payout_spy)

    @contextmanager
    def writer(_conn, *, canonical):
        assert canonical is True
        trade_conn.execute("BEGIN IMMEDIATE")
        yield time.monotonic() + 5

    monkeypatch.setattr(resolver, "_settlement_writer_transaction", writer)

    settled = []

    def settle(_conn, _portfolio, city_name, target_date, *_args, **_kwargs):
        settled.append((city_name, target_date))
        return 1

    monkeypatch.setattr(hv, "_settle_positions", settle)
    monkeypatch.setattr(
        "src.state.decision_chain.store_settlement_records", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "src.state.canonical_write.commit_then_export",
        lambda conn, *, db_op, json_exports: db_op(),
    )
    monkeypatch.setattr("src.state.portfolio.save_portfolio", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.strategy_tracker.get_tracker", lambda: MagicMock())
    monkeypatch.setattr("src.state.strategy_tracker.save_tracker", lambda *a, **kw: None)

    result = resolver.resolve_pnl_for_settled_markets(trade_conn, forecasts_conn)

    assert len(payout_calls) == 2
    # Pre-lease call scans the real unverified-key scope looking for a candidate.
    assert payout_calls[0] == {("CityB", "2026-08-12", "high")}
    # In-lease re-read is scoped to the pre-lease candidates found (none) --
    # empty, not the parent code's full unverified-key set.
    assert payout_calls[1] == set()
    assert settled == [("CityA", "2026-08-12")]
    assert result["positions_settled"] == 1


def test_in_lease_payout_reread_scoped_to_pre_lease_candidate_only(
    trade_conn, monkeypatch
):
    """Two open, unverified conditions A/B; a finalized chain payout exists
    only for A pre-lease. The in-lease payout re-read must receive exactly
    A's key -- B is never re-scanned -- and only A settles."""
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver

    ensure_payout_table(trade_conn)
    init_snapshot_schema(trade_conn, include_latest=False)
    condition_a = "0x" + "1" * 64
    condition_b = "0x" + "2" * 64
    portfolio, pos_a = _winning_position(
        trade_id="cond-a", city="CityA", target_date="2026-08-12"
    )
    pos_a.condition_id = condition_a
    pos_a.token_id = "yes-token-a"
    pos_a.no_token_id = "no-token-a"
    _, pos_b = _winning_position(
        trade_id="cond-b", city="CityB", target_date="2026-08-12"
    )
    pos_b.condition_id = condition_b
    pos_b.token_id = "yes-token-b"
    pos_b.no_token_id = "no-token-b"
    portfolio.positions = [pos_a, pos_b]

    for pos in (pos_a, pos_b):
        trade_conn.execute(
            """INSERT INTO position_current (
                   position_id, phase, city, target_date, temperature_metric,
                   condition_id, updated_at
               ) VALUES (?, 'active', ?, ?, 'high', ?, ?)""",
            (pos.trade_id, pos.city, pos.target_date, pos.condition_id, "before-writer"),
        )
    trade_conn.commit()

    # Snapshot + a complete, terminal, finalized binary payout vector for A only.
    trade_conn.execute(
        """INSERT INTO executable_market_snapshots (
               snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
               question_id, yes_token_id, no_token_id, enable_orderbook, active,
               closed, min_tick_size, min_order_size, fee_details_json,
               token_map_json, neg_risk, orderbook_top_bid, orderbook_top_ask,
               orderbook_depth_json, raw_gamma_payload_hash,
               raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
               captured_at, freshness_deadline
           ) VALUES (
               'snap-a', 'gamma', 'event', 'citya-aug-12', ?, 'question',
               'yes-token-a', 'no-token-a', 1, 0, 1, '0.001', '1', '{}',
               '{"YES":"yes-token-a","NO":"no-token-a"}', 0, '0', '0', '{}',
               'g', 'c', 'b', 'CHAIN', '2026-08-13T07:20:00+00:00',
               '2026-08-13T07:21:00+00:00'
           )""",
        (condition_a,),
    )
    _insert_payout(
        trade_conn, condition_id=condition_a, outcome_index=0, numerator=0,
        block_number=101, block_hash="0x101",
    )
    _insert_payout(
        trade_conn, condition_id=condition_a, outcome_index=1, numerator=1,
        block_number=100, block_hash="0x100",
    )
    trade_conn.commit()
    # B has no snapshot and no payout_observations rows at all.

    forecasts_conn = _empty_settlement_outcomes_conn()

    monkeypatch.setattr("src.state.portfolio.load_portfolio", lambda *a, **kw: portfolio)
    monkeypatch.setattr(resolver, "_read_venue_resolved_settlement_rows", lambda *a, **kw: [])
    monkeypatch.setattr(resolver, "_is_canonical_trade_connection", lambda _c: True)

    payout_calls: list[set] = []
    original_payout_reader = resolver._read_finalized_payout_settlement_rows

    def payout_spy(conn, portfolio_arg, keys, snapshots):
        keys = set(keys)
        payout_calls.append(keys)
        return original_payout_reader(conn, portfolio_arg, keys, snapshots)

    monkeypatch.setattr(resolver, "_read_finalized_payout_settlement_rows", payout_spy)

    @contextmanager
    def writer(_conn, *, canonical):
        assert canonical is True
        trade_conn.execute("BEGIN IMMEDIATE")
        yield time.monotonic() + 5

    monkeypatch.setattr(resolver, "_settlement_writer_transaction", writer)

    settled = []

    def settle(_conn, _portfolio, city_name, target_date, *_args, **_kwargs):
        settled.append((city_name, target_date))
        return 1

    monkeypatch.setattr(hv, "_settle_positions", settle)
    monkeypatch.setattr(
        "src.state.decision_chain.store_settlement_records", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "src.state.canonical_write.commit_then_export",
        lambda conn, *, db_op, json_exports: db_op(),
    )
    monkeypatch.setattr("src.state.portfolio.save_portfolio", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.strategy_tracker.get_tracker", lambda: MagicMock())
    monkeypatch.setattr("src.state.strategy_tracker.save_tracker", lambda *a, **kw: None)

    result = resolver.resolve_pnl_for_settled_markets(trade_conn, forecasts_conn)

    both_keys = {("CityA", "2026-08-12", "high"), ("CityB", "2026-08-12", "high")}
    assert len(payout_calls) == 2
    # Pre-lease call scans both unverified keys looking for a candidate.
    assert payout_calls[0] == both_keys
    # In-lease re-read is scoped to A only -- B was never a candidate pre-lease
    # and is never re-scanned inside the lease.
    assert payout_calls[1] == {("CityA", "2026-08-12", "high")}
    assert settled == [("CityA", "2026-08-12")]
    assert result["positions_settled"] == 1


def test_in_lease_payout_reread_still_catches_retraction_between_reads(
    trade_conn, monkeypatch
):
    """TOCTOU guard preserved: A's payout is retracted (one outcome slot
    deleted) between the pre-lease read and the in-lease re-read. The
    narrowing must not skip re-reading A -- since A was itself the pre-lease
    candidate -- so the in-lease read still runs real SQL for A, sees the now
    -incomplete vector, and A must NOT settle. No partial write."""
    from src.execution import harvester as hv
    from src.execution import harvester_pnl_resolver as resolver

    ensure_payout_table(trade_conn)
    init_snapshot_schema(trade_conn, include_latest=False)
    condition_a = "0x" + "3" * 64
    portfolio, pos_a = _winning_position(
        trade_id="cond-a-toctou", city="CityA", target_date="2026-08-12"
    )
    pos_a.condition_id = condition_a
    pos_a.token_id = "yes-token-a"
    pos_a.no_token_id = "no-token-a"
    portfolio.positions = [pos_a]

    trade_conn.execute(
        """INSERT INTO position_current (
               position_id, phase, city, target_date, temperature_metric,
               condition_id, updated_at
           ) VALUES (?, 'active', ?, ?, 'high', ?, ?)""",
        (pos_a.trade_id, pos_a.city, pos_a.target_date, pos_a.condition_id, "before-writer"),
    )
    trade_conn.execute(
        """INSERT INTO executable_market_snapshots (
               snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
               question_id, yes_token_id, no_token_id, enable_orderbook, active,
               closed, min_tick_size, min_order_size, fee_details_json,
               token_map_json, neg_risk, orderbook_top_bid, orderbook_top_ask,
               orderbook_depth_json, raw_gamma_payload_hash,
               raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
               captured_at, freshness_deadline
           ) VALUES (
               'snap-a-toctou', 'gamma', 'event', 'citya-aug-12', ?, 'question',
               'yes-token-a', 'no-token-a', 1, 0, 1, '0.001', '1', '{}',
               '{"YES":"yes-token-a","NO":"no-token-a"}', 0, '0', '0', '{}',
               'g', 'c', 'b', 'CHAIN', '2026-08-13T07:20:00+00:00',
               '2026-08-13T07:21:00+00:00'
           )""",
        (condition_a,),
    )
    _insert_payout(
        trade_conn, condition_id=condition_a, outcome_index=0, numerator=0,
        block_number=101, block_hash="0x101",
    )
    _insert_payout(
        trade_conn, condition_id=condition_a, outcome_index=1, numerator=1,
        block_number=100, block_hash="0x100",
    )
    trade_conn.commit()

    forecasts_conn = _empty_settlement_outcomes_conn()

    monkeypatch.setattr("src.state.portfolio.load_portfolio", lambda *a, **kw: portfolio)
    monkeypatch.setattr(resolver, "_read_venue_resolved_settlement_rows", lambda *a, **kw: [])
    monkeypatch.setattr(resolver, "_is_canonical_trade_connection", lambda _c: True)

    payout_calls: list[set] = []
    original_payout_reader = resolver._read_finalized_payout_settlement_rows

    def payout_spy(conn, portfolio_arg, keys, snapshots):
        keys = set(keys)
        payout_calls.append(keys)
        return original_payout_reader(conn, portfolio_arg, keys, snapshots)

    monkeypatch.setattr(resolver, "_read_finalized_payout_settlement_rows", payout_spy)

    @contextmanager
    def writer(_conn, *, canonical):
        assert canonical is True
        # Concurrent retraction: a new (append-only) outcome_index=1
        # observation supersedes the terminal one with a non-terminal state,
        # between the pre-lease discovery read and the in-lease re-read --
        # breaking the complete {0, 1} terminal vector the reader requires.
        _insert_payout(
            trade_conn, condition_id=condition_a, outcome_index=1, numerator=0,
            denominator=0, state="UNRESOLVED", block_number=102, block_hash="0x102",
        )
        trade_conn.commit()
        trade_conn.execute("BEGIN IMMEDIATE")
        yield time.monotonic() + 5

    monkeypatch.setattr(resolver, "_settlement_writer_transaction", writer)

    settled = []

    def settle(_conn, _portfolio, city_name, target_date, *_args, **_kwargs):
        settled.append((city_name, target_date))
        return 1

    monkeypatch.setattr(hv, "_settle_positions", settle)
    store_calls = []
    monkeypatch.setattr(
        "src.state.decision_chain.store_settlement_records",
        lambda records, **kw: store_calls.append(list(records)),
    )
    monkeypatch.setattr(
        "src.state.canonical_write.commit_then_export",
        lambda conn, *, db_op, json_exports: db_op(),
    )
    monkeypatch.setattr("src.state.portfolio.save_portfolio", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.strategy_tracker.get_tracker", lambda: MagicMock())
    monkeypatch.setattr("src.state.strategy_tracker.save_tracker", lambda *a, **kw: None)

    result = resolver.resolve_pnl_for_settled_markets(trade_conn, forecasts_conn)

    # In-lease read is still scoped to A (A was the pre-lease candidate) --
    # the narrowing does not skip A -- but the fresh read now sees the
    # incomplete vector and rejects it.
    assert payout_calls[1] == {("CityA", "2026-08-12", "high")}
    assert settled == []
    assert store_calls == [] or all(len(c) == 0 for c in store_calls)
    assert result["positions_settled"] == 0
    assert result["status"] == "awaiting_truth_writer"


# --- X-BK: _canonical_position_versions fingerprint reduction -------------
#
# The write lease is a 5s budget. _canonical_position_versions used to
# fingerprint each applied position with THREE correlated subqueries over
# position_events (COUNT(*), MAX(sequence_no), MAX(occurred_at)) -- COUNT(*)
# and MAX(occurred_at) are full index-range walks over the position's whole
# event history (positions can carry hundreds of MONITOR_REFRESHED rows),
# not index seeks. The tests below prove the reduced fingerprint (position_
# current columns + MAX(sequence_no) only, read via a single tail seek) has
# the same discriminating power as the original for the TOCTOU guard in
# _canonical_row_is_stable, including in the presence of back-dated
# occurred_at values (see _canonical_position_versions docstring for the
# command_recovery.py sites that back-date occurred_at).


def _old_canonical_position_versions(trade_conn, keys):
    """Reference copy of the pre-fix (3-correlated-subquery) fingerprint
    query, kept ONLY so test_canonical_fingerprint_equivalence_across_event_
    volumes can assert the reduced fingerprint's change/no-change verdict
    matches this one's, across positions with 1/40/400 events. Do not use
    this in production code -- it is the O(events) query the fix replaces.
    """
    from src.execution.harvester_pnl_resolver import _row_value

    key_list = sorted(keys)
    if not key_list:
        return {}
    placeholders = ",".join("(?, ?, ?)" for _ in key_list)
    params = [part for key in key_list for part in key]
    rows = trade_conn.execute(
        f"""WITH requested(city, target_date, temperature_metric) AS (
                    VALUES {placeholders}
                )
                SELECT pc.*,
                    (SELECT COUNT(*) FROM position_events pe
                      WHERE pe.position_id = pc.position_id) AS event_count,
                    (SELECT MAX(pe.sequence_no) FROM position_events pe
                      WHERE pe.position_id = pc.position_id) AS max_event_sequence,
                    (SELECT MAX(pe.occurred_at) FROM position_events pe
                      WHERE pe.position_id = pc.position_id) AS max_event_time
                  FROM position_current pc
                  JOIN requested r
                    ON r.city = pc.city
                   AND r.target_date = pc.target_date
                   AND r.temperature_metric = COALESCE(pc.temperature_metric, 'high')
                 WHERE pc.phase IN ('active', 'day0_window', 'pending_exit',
                                    'economically_closed')""",
        params,
    ).fetchall()
    return {
        str(_row_value(row, "position_id", 0, "") or ""): tuple(row)
        for row in rows
    }


def _insert_position_event(trade_conn, position_id, sequence_no, occurred_at, event_id=None):
    trade_conn.execute(
        """INSERT INTO position_events (
               event_id, position_id, event_version, sequence_no, event_type,
               occurred_at, phase_before, phase_after, source_module,
               payload_json, caused_by, env
           ) VALUES (?, ?, 1, ?, 'MONITOR_REFRESHED', ?, 'active', 'active',
                     'tests.harvester_resolver', '{}', 'monitor_refresh', 'live')""",
        (event_id or f"{position_id}-ev{sequence_no}", position_id, sequence_no, occurred_at),
    )


def test_canonical_fingerprint_equivalence_across_event_volumes(trade_conn):
    """The reduced fingerprint changes iff the reference (pre-fix) fingerprint
    would have, for positions carrying 1, 40 and 400 events -- an append
    changes both, no append changes neither, and a same-sequence event
    cannot exist (UNIQUE(position_id, sequence_no))."""
    from src.execution import harvester_pnl_resolver as resolver

    city, target_date, metric = "CityA", "2026-06-01", "high"
    sizes = {"small": 1, "mid": 40, "large": 400}
    for suffix, n_events in sizes.items():
        position_id = f"pos-{suffix}"
        trade_conn.execute(
            """INSERT INTO position_current (
                   position_id, phase, city, target_date, temperature_metric, updated_at
               ) VALUES (?, 'active', ?, ?, ?, 't')""",
            (position_id, city, target_date, metric),
        )
        for seq in range(1, n_events + 1):
            _insert_position_event(
                trade_conn, position_id, seq, f"2026-06-01T00:{seq % 60:02d}:00Z"
            )
    trade_conn.commit()

    keys = {(city, target_date, metric)}
    old_before = _old_canonical_position_versions(trade_conn, keys)
    new_before = resolver._canonical_position_versions(trade_conn, keys)

    # Append one more event to every position -> both fingerprints must change.
    for suffix, n_events in sizes.items():
        position_id = f"pos-{suffix}"
        _insert_position_event(
            trade_conn, position_id, n_events + 1, "2026-06-01T23:59:00Z",
            event_id=f"{position_id}-appended",
        )
    trade_conn.commit()

    old_after = _old_canonical_position_versions(trade_conn, keys)
    new_after = resolver._canonical_position_versions(trade_conn, keys)

    for suffix in sizes:
        position_id = f"pos-{suffix}"
        old_changed = old_before[position_id] != old_after[position_id]
        new_changed = (
            new_before[position_id]["fingerprint"]
            != new_after[position_id]["fingerprint"]
        )
        assert old_changed is True, f"{position_id}: reference fingerprint did not move"
        assert new_changed is True, f"{position_id}: reduced fingerprint did not move"
        assert old_changed == new_changed

    # No further change -> both fingerprints identical on a second read.
    old_again = _old_canonical_position_versions(trade_conn, keys)
    new_again = resolver._canonical_position_versions(trade_conn, keys)
    for suffix in sizes:
        position_id = f"pos-{suffix}"
        assert old_after[position_id] == old_again[position_id]
        assert (
            new_after[position_id]["fingerprint"]
            == new_again[position_id]["fingerprint"]
        )

    # A same-sequence event cannot exist by UNIQUE(position_id, sequence_no).
    with pytest.raises(sqlite3.IntegrityError):
        _insert_position_event(
            trade_conn, "pos-small", 1, "2026-06-01T00:00:00Z", event_id="pos-small-dup-seq"
        )


def test_backdated_occurred_at_still_detected(trade_conn):
    """A recovered event whose occurred_at is EARLIER than the previous
    event's occurred_at for that position -- the back-dating pattern in
    command_recovery.py's filled-entry repair, where occurred_at is sourced
    from a venue timestamp (fill_observed_at) discovered after the fact --
    still changes the fingerprint and still fails
    _canonical_row_is_stable's TOCTOU check. This is the test that proves
    the design: sequence_no, not occurred_at, is the change detector."""
    from src.execution import harvester_pnl_resolver as resolver

    city, target_date, metric = "CityB", "2026-06-02", "high"
    trade_conn.execute(
        """INSERT INTO position_current (
               position_id, phase, city, target_date, temperature_metric, updated_at
           ) VALUES ('pos-backdate', 'active', ?, ?, ?, 't')""",
        (city, target_date, metric),
    )
    _insert_position_event(trade_conn, "pos-backdate", 1, "2026-06-02T12:00:00Z")
    trade_conn.commit()

    keys = {(city, target_date, metric)}
    before = resolver._canonical_position_versions(trade_conn, keys)

    # sequence_no=2 carries occurred_at EARLIER than sequence_no=1's --
    # command_recovery.py:6671-6726 appends ENTRY_ORDER_FILLED this way,
    # with occurred_at=candidate["fill_observed_at"] (the venue's true fill
    # time) and no check against the previous row's occurred_at.
    _insert_position_event(trade_conn, "pos-backdate", 2, "2026-06-02T05:00:00Z")
    trade_conn.commit()

    after = resolver._canonical_position_versions(trade_conn, keys)

    assert (
        before["pos-backdate"]["fingerprint"] != after["pos-backdate"]["fingerprint"]
    ), "back-dated append must still change the fingerprint"
    assert after["pos-backdate"]["fingerprint"][-1] == 2, "max_event_sequence must advance to 2"

    row = trade_conn.execute(
        "SELECT ? AS city, ? AS target_date, ? AS temperature_metric, "
        "'family' AS settlement_scope, '' AS condition_id",
        (city, target_date, metric),
    ).fetchone()
    assert resolver._canonical_row_is_stable(row, before, after) is False, (
        "TOCTOU guard must still reject a row whose position changed, "
        "even when the change is back-dated"
    )


class _SqlCapture:
    """Wraps a sqlite3 connection to record the last SQL text and params
    passed to .execute(), so a test can re-run it under EXPLAIN QUERY PLAN."""

    def __init__(self, conn):
        self._conn = conn
        self.last_sql = None
        self.last_params = None

    def execute(self, sql, params=()):
        self.last_sql = sql
        self.last_params = tuple(params)
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_canonical_position_versions_query_plan_is_one_seek_no_count(trade_conn):
    """For a 3-key request (the applied_keys shape at
    harvester_pnl_resolver.py's in-lease re-fingerprint call), the fingerprint
    query must contain no COUNT aggregate, no TEMP B-TREE, exactly one
    correlated subquery against position_events, and that subquery must be a
    SEARCH (index seek), never a SCAN.

    Note (R-BK, 2026-09-14): this test's ``trade_conn`` fixture builds its
    schema via plain ``init_schema()``, which creates position_current from
    the kernel SQL file with NO named index at all (not even the pre-existing
    idx_position_current_phase_quote) -- so it cannot exercise
    idx_position_current_city_date_metric, which (matching that sibling
    index's own precedent) lives only in _TRADE_CLASS_DDL, the schema
    init_schema_trade_only applies to the real trade DB. See
    test_canonical_position_versions_join_uses_city_date_metric_index_at_realistic_scale
    in test_db.py for that EXPLAIN assertion against the real init path."""
    from src.execution import harvester_pnl_resolver as resolver

    keys = {
        ("CityA", "2026-06-01", "high"),
        ("CityB", "2026-06-02", "high"),
        ("CityC", "2026-06-03", "low"),
    }
    for i, (city, target_date, metric) in enumerate(sorted(keys)):
        position_id = f"pos-plan-{i}"
        trade_conn.execute(
            """INSERT INTO position_current (
                   position_id, phase, city, target_date, temperature_metric, updated_at
               ) VALUES (?, 'active', ?, ?, ?, 't')""",
            (position_id, city, target_date, metric),
        )
        for seq in range(1, 6):
            _insert_position_event(
                trade_conn, position_id, seq, f"2026-06-0{i + 1}T00:{seq:02d}:00Z"
            )
    trade_conn.commit()

    spy = _SqlCapture(trade_conn)
    resolver._canonical_position_versions(spy, keys)
    assert spy.last_sql is not None

    compact_sql = spy.last_sql.upper().replace(" ", "").replace("\n", "")
    assert "COUNT(" not in compact_sql, "fingerprint query must not aggregate COUNT(*)"

    plan_rows = trade_conn.execute(
        f"EXPLAIN QUERY PLAN {spy.last_sql}", spy.last_params
    ).fetchall()
    details = [str(row["detail"]) for row in plan_rows]
    plan_text = "\n".join(details).upper()

    assert "TEMP B-TREE" not in plan_text
    correlated = [d for d in details if "CORRELATED SCALAR SUBQUERY" in d.upper()]
    assert len(correlated) == 1, f"expected exactly one position_events subquery: {details}"

    pe_search_lines = [d for d in details if d.upper().startswith("SEARCH PE ")]
    assert pe_search_lines, f"expected a SEARCH on position_events (pe): {details}"
    for line in pe_search_lines:
        assert "SCAN" not in line.upper(), f"position_events access is not a seek: {line}"
