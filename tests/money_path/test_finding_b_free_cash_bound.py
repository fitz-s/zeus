# Created: 2026-06-12
# Last reused/audited: 2026-10-08
# Authority basis: FINDING-B + ROOT money authenticity repair PLAN:21798.
"""Free cash is a bound, not probability/selection or submit-readiness authority.

Normal HKO entity custody, writer, public ENTRY/HELD and RESET are reused from
the owning materializer relationship. Each cash scenario runs the actual global
mean optimizer and consumes its persisted receipt. Private books/collateral are
controlled external inputs; no source probability or readiness is fabricated.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest

from src.events.reactor import _is_transient_money_path_reason


def _body_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _cash_book_response(token, condition, depth, tick, min_size):
    """CLOB-shaped private response; the owning capture derives its body hash."""
    return {"asset_id": token, "market": condition, **depth,
            "tick_size": tick, "min_order_size": min_size}


def _cash_raw_books(rows):
    return {row["selected_outcome_token_id"]: _cash_book_response(
        row["selected_outcome_token_id"], row["condition_id"],
        json.loads(row["orderbook_depth_json"])[row["outcome_label"]],
        row["min_tick_size"], row["min_order_size"])
        for row in rows}


def _normal_cash_matrix(*, conn, request, bundle, monkeypatch):
    """The normal HKO RESET certificate feeds actual global selection/receipts."""
    from src.config import runtime_cities_by_name
    from src.data.forecast_target_contract import compute_target_local_day_window_utc
    from src.data import replacement_forecast_bundle_reader as reader
    city = runtime_cities_by_name()[request.city]
    decision_time = request.computed_at
    owning_datetime = reader.datetime
    class ClockType(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)
    class ConsumerClock(owning_datetime, metaclass=ClockType):
        @classmethod
        def now(cls, tz=None):
            return decision_time.astimezone(tz) if tz else decision_time.replace(tzinfo=None)
    monkeypatch.setattr(reader, "datetime", ConsumerClock)
    target_window = compute_target_local_day_window_utc(
        city_timezone=city.timezone, target_local_date=request.target_date)
    from src.events.triggers.forecast_snapshot_ready import ForecastSnapshotReadyTrigger
    from src.state.db import init_schema_trade_only
    from src.state.snapshot_repo import init_snapshot_schema, insert_snapshot
    from src.state.schema.family_rebalance_intents_schema import ensure_table as ensure_rebalance_schema
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from dataclasses import replace
    from decimal import Decimal
    from src.engine import event_reactor_adapter as adapter
    from src.engine import global_auction_universe as universe
    from src.engine.global_single_order_auction import select_prepared_global_auction
    from src.events.candidate_binding import weather_family_id
    from src.state import collateral_ledger as collateral
    from src.state.portfolio import load_runtime_open_portfolio
    from src.engine.global_batch_runtime import (
        _bind_selection_holdings, _store_global_auction_receipt, _bind_stored_global_auction_receipt,
    )
    from tests.fakes.polymarket_v2 import FakeClock, FakeCollateralLedger, FakePolymarketVenue
    # Controlled market contracts use this certificate's exact partition,
    # not a foreign city's labels or a selected subset of its probability.
    for item in request.bins:
        condition = "0x" + hashlib.sha256(item.bin_id.encode()).hexdigest()
        conn.execute("""INSERT INTO market_events (
            market_slug, city, target_date, temperature_metric, condition_id,
            token_id, range_label, range_low, range_high, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"cash-hko-{request.target_date}-{item.bin_id}", city.name,
             request.target_date.isoformat(), request.temperature_metric,
             condition, f"private-hko-yes-{item.bin_id}", item.bin_id,
             item.lower_c, item.upper_c, decision_time.isoformat()))
    conn.commit()
    events = ForecastSnapshotReadyTrigger(None).build_committed_snapshot_events(
        forecasts_conn=conn, decision_time=decision_time,
        received_at=decision_time.isoformat(), restrict_to_families={
            (city.name, request.target_date.isoformat(), request.temperature_metric)})
    assert len(events) == 1, events
    event = events[0]
    assert event.event_type == "FORECAST_SNAPSHOT_READY"
    markets = conn.execute(
        "SELECT condition_id,token_id,range_label FROM market_events "
        "WHERE city=? AND target_date=? AND temperature_metric=? ORDER BY range_low",
        (city.name, request.target_date.isoformat(), request.temperature_metric)).fetchall()
    assert len(markets) == len(bundle.q) == 3
    with closing(sqlite3.connect(":memory:")) as trade:
        trade.row_factory = sqlite3.Row
        init_schema_trade_only(trade)
        init_snapshot_schema(trade)
        ensure_rebalance_schema(trade)
        # Prices are private executable inputs, not probability authority.
        # Full-family asks sum to one; the left shoulder offers positive edge.
        for index, market in enumerate(markets):
            yes = market["token_id"]
            no = f"private-hko-no-{index}"
            yes_ask = ("0.06", "0.45", "0.49")[index]
            no_ask = ("0.94", "0.55", "0.51")[index]
            depth = json.dumps({side: {
                "asks": [{"price": ask, "size": "1000"}],
                "bids": [{"price": "0.05", "size": "1000"}]}
                for side, ask in (("YES", yes_ask), ("NO", no_ask))})
            for side, token, ask in (("YES", yes, yes_ask), ("NO", no, no_ask)):
                row = dict(snapshot_id=f"cash-{index}-{side}", gamma_market_id=f"cash-market-{index}",
                    event_id=f"private-hko-{request.target_date}", event_slug=f"private-hko-{request.temperature_metric}",
                    condition_id=market["condition_id"], question_id=f"cash-question-{index}",
                    yes_token_id=yes, no_token_id=no, selected_outcome_token_id=token,
                    outcome_label=side, enable_orderbook=1, active=1, closed=0, accepting_orders=1,
                    min_tick_size="0.01", min_order_size="5", fee_details_json='{"fee_rate_fraction":0}',
                    token_map_json=json.dumps({"yes": yes, "no": no}), neg_risk=0,
                    orderbook_top_bid="0.05", orderbook_top_ask=ask, orderbook_depth_json=depth,
                    raw_gamma_payload_hash=_body_hash({"market": dict(market), "active": True, "closed": False}),
                    raw_clob_market_info_hash=_body_hash({"condition_id": market["condition_id"], "tick_size": "0.01", "min_order_size": "5"}),
                    raw_orderbook_hash=universe._canonical_raw_book_hash(_cash_book_response(
                        token, market["condition_id"], json.loads(depth)[side], "0.01", "5")), authority_tier="CLOB",
                    captured_at=decision_time.isoformat(),
                    freshness_deadline=(decision_time+timedelta(seconds=180)).isoformat())
                row.update(market_start_at=None, market_end_at=target_window.end_utc,
                           market_close_at=target_window.end_utc, sports_start_at=None, rfqe=None)
                row["fee_details"] = json.loads(row.pop("fee_details_json"))
                row["token_map_raw"] = json.loads(row.pop("token_map_json"))
                row["orderbook_depth_jsonb"] = row.pop("orderbook_depth_json")
                for key in ("min_tick_size", "min_order_size", "orderbook_top_bid", "orderbook_top_ask"):
                    if row[key] is not None:
                        row[key] = Decimal(row[key])
                row["captured_at"] = decision_time
                row["freshness_deadline"] = decision_time + timedelta(seconds=180)
                insert_snapshot(trade, ExecutableMarketSnapshot(**row))
        trade.commit()
        live = adapter.event_bound_live_adapter_from_trade_conn(
            trade, forecast_conn=conn, topology_conn=conn, calibration_conn=conn,
            get_current_level=lambda: adapter.RiskLevel.GREEN,
            bankroll_usd_provider=lambda: 1000.)
        preparation = live.prepare_global_event(event, decision_time)
        assert preparation.prepared_global_family is not None, preparation.reason
        prepared = preparation.prepared_global_family
        tokens = {market["condition_id"]: (market["token_id"], f"private-hko-no-{index}")
                  for index, market in enumerate(markets)}
        probability = universe._rebind_probability_witness_tokens(
            prepared.probability_witness, token_map_by_condition=tokens,
            required_token_ids=frozenset(token for pair in tokens.values() for token in pair))
        prepared = replace(prepared, probability_witness=probability)
        assert probability.family_key == weather_family_id(
            city=bundle.city, target_date=bundle.target_date,
            metric=bundle.temperature_metric)
        assert probability.posterior_identity_hash == bundle.posterior_identity_hash
        assert probability.source_truth_identity == adapter.stable_hash({
            "dependency_hash": bundle.dependency_hash,
            "posterior_config_hash": bundle.posterior_config_hash,
            "source_cycle_time": bundle.source_cycle_time,
            "source_available_at": bundle.source_available_at})
        bin_by_condition = {market["condition_id"]: market["range_label"]
                            for market in markets}
        assert {bin_by_condition[binding.condition_id] for binding in probability.bindings} == set(bundle.q)
        assert list(probability.yes_point_q) == pytest.approx([
            bundle.q[bin_by_condition[binding.condition_id]]
            for binding in probability.bindings])
        books = _cash_raw_books(trade.execute("SELECT * FROM executable_market_snapshots"))
        epoch = universe.capture_current_global_book_epoch(
            trade, probability_witnesses={probability.family_key: probability},
            get_books=lambda token_ids, **kwargs: {token: books[token] for token in token_ids},
            clock=lambda: decision_time, max_age=timedelta(seconds=180))
        monkeypatch.setattr(collateral, "datetime", reader.datetime)
        venue = FakePolymarketVenue(ledger=FakeCollateralLedger(), clock=FakeClock(decision_time))
        collateral.CollateralLedger(trade).refresh(venue)
        trade.commit()
        portfolio = load_runtime_open_portfolio(trade)
        wealth = universe.current_portfolio_wealth_witness(
            trade, decision_at_utc=decision_time, max_age=timedelta(seconds=180), portfolio_state=portfolio)
        prepared_by_event = _bind_selection_holdings({event.event_id: prepared},
            portfolio_state=portfolio, wealth_witness=wealth,
            required_token_ids_by_family={probability.family_key: frozenset(books)})
        scope = universe.current_global_auction_scope_from_events((event,), captured_at_utc=decision_time)
        receipts = {}
        for name, extra in (("unwired", {}),
                            ("baseline", {"free_cash_usd_provider": lambda: float(wealth.spendable_cash_usd)}),
                            ("large", {"free_cash_usd_provider": lambda: 1000.}),
                            ("small", {"free_cash_usd_provider": lambda: 5.}),
                            ("missing", {"free_cash_usd_provider": lambda: None})):
            # An injected cash bound is a conservative capital limit, not a
            # rewritten q/band or a forced candidate. The actual mean optimizer
            # independently selects the lawful fixed proposal.
            selected = select_prepared_global_auction(
                prepared_by_event, selection_epoch_identity=f"cash-{name}",
                selection_cut_at_utc=decision_time, current_scope=scope,
                current_scope_identity_resolver=lambda: scope.scope_identity,
                venue_universe_identity=epoch.witness_identity,
                current_venue_universe_identity_resolver=lambda: epoch.witness_identity,
                universe_max_age=timedelta(seconds=180),
                current_probability_resolver=lambda _: adapter.current_global_probability_authority(
                    conn, event, probability, decision_time=decision_time),
                current_execution_resolver=lambda candidate: epoch.execution_authority(candidate, checked_at_utc=decision_time),
                current_wealth_identity_resolver=lambda: wealth.economic_identity,
                wealth_witness=wealth, capital_limit_usd=Decimal("5" if name == "small" else "1000"),
                fractional_kelly_multiplier=Decimal(str(adapter._runtime_kelly_multiplier())),
                decision_at_utc=decision_time, book_epoch=epoch)
            assert selected.actuation is not None, selected.decision.no_trade_reason
            row_id = _store_global_auction_receipt(trade, selected=selected,
                selection_epoch_identity=f"cash-{name}", selection_cut_at_utc=decision_time,
                decision_at_utc=decision_time,
                probability_manifest=((probability.family_key, probability.witness_identity),),
                full_scope_identity=scope.scope_identity, full_scope_family_keys=scope.family_keys,
                probability_ineligible_by_family={}, book_epoch_identity=epoch.witness_identity,
                book_asset_count=len(epoch.assets)+len(epoch.sell_assets), book_asset_states=epoch.asset_states,
                wealth_witness=wealth, fractional_kelly_multiplier=Decimal(str(adapter._runtime_kelly_multiplier())),
                book_captured_at_utc=epoch.captured_at_utc, book_max_age=epoch.max_age,
                probability_witnesses={probability.family_key: probability}, book_epoch=epoch)
            trade.commit()
            selected = _bind_stored_global_auction_receipt(trade, selected=selected, decision_log_id=row_id)
            assert selected.actuation.auction_receipt_ref.decision_log_id == row_id
            receipts[name] = adapter.build_event_bound_no_submit_receipt(
                event, trade_conn=trade, forecast_conn=conn, topology_conn=conn,
                calibration_conn=conn, decision_time=decision_time,
                get_current_level=lambda: adapter.RiskLevel.GREEN,
                bankroll_usd_provider=lambda: 1000., reserve_on_pass=False,
                global_actuation=selected.actuation, **extra)
        return receipts


@pytest.fixture
def cash_matrix(tmp_path):
    """One immutable qualified q, actual independent selection for each cash bound."""
    from contextlib import ExitStack
    from tests import test_replacement_forecast_materializer as normal
    from src.state.db import get_world_connection, init_schema_world_only

    # The autouse TI-1 fixture routes this factory to the current test's
    # private WORLD; real empty control tables, not a fabricated gate verdict.
    with closing(get_world_connection()) as world:
        init_schema_world_only(world)
        world.commit()

    root = tmp_path / "cash-normal-hko"
    root.mkdir()
    with pytest.MonkeyPatch.context() as patch, ExitStack() as stack:
        patch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
        native = normal._hko_native_surfaces.__wrapped__(root, patch)
        next(native)
        stack.callback(native.close)
        source = normal._hko_source_surface.__wrapped__(root, patch, None)
        next(source)
        stack.callback(source.close)
        matrices = []
        def consume(**context):
            matrices.append(_normal_cash_matrix(**context, monkeypatch=patch))
        normal._normal_hko_day1_qualified_context(
            root, patch, on_qualified_context=consume)
        assert len(matrices) == 1
        return matrices[0]


def test_free_cash_provider_binds_stake_to_free_cash(cash_matrix):
    """The actual mean-selected fixed proposal must respect the five-dollar bound.

    Re-optimization may select a different token, so per-token size monotonicity
    against the unconstrained family optimum is not a cash law.
    """
    receipt = cash_matrix["small"]
    assert receipt.kelly_pass is True, receipt.reason
    assert receipt.kelly_size_usd is not None
    assert receipt.kelly_size_usd <= 5.0 + 1e-9
    assert receipt.kelly_size_usd > 0.0


def test_free_cash_above_stake_does_not_inflate(cash_matrix):
    """When free cash exceeds the fractional-Kelly stake, the bound is a no-op (min):
    the stake stays at its equity-scaled value, never raised to free cash."""
    receipt = cash_matrix["large"]
    assert receipt.kelly_pass is True, receipt.reason
    assert receipt.kelly_size_usd is not None
    assert 0.0 < receipt.kelly_size_usd < 1000.0
    baseline = cash_matrix["baseline"]
    assert baseline.kelly_pass is True, baseline.reason
    assert receipt.kelly_size_usd == pytest.approx(baseline.kelly_size_usd)


def test_free_cash_unresolvable_under_live_provider_fails_closed_transient(cash_matrix):
    """A wired free-cash authority that returns None is a TYPED FAULT, never a silent
    unclamped submit. The receipt does not pass, and the reason is classified TRANSIENT
    (requeue) so the next warm cycle re-resolves the wallet rather than terminal-burning."""
    receipt = cash_matrix["missing"]
    assert receipt.kelly_pass is False
    assert "BANKROLL_FREE_CASH_MISSING" in (receipt.reason or "")
    assert _is_transient_money_path_reason(receipt.reason) is True


def test_no_free_cash_provider_rejects_global_actuation(cash_matrix):
    """A real global selection cannot act without its current free-cash binding."""
    receipt = cash_matrix["unwired"]
    assert receipt.kelly_pass is False
    assert receipt.kelly_size_usd == 0.0
    assert receipt.reason.startswith("KELLY_PROOF_MISSING:")
    assert "GLOBAL_ACTUATION_FREE_CASH_SUPERSEDED" in receipt.reason
    assert receipt.submitted is False
