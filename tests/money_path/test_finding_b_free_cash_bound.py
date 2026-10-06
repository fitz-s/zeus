# Created: 2026-06-12
# Last reused/audited: 2026-10-06
# Authority basis: external deep code review 2026-06-12 FINDING-B (operator direct-fix
#   order). The free-cash one-time bound silently VANISHED (free_cash_usd=None, no
#   clamp) whenever a bankroll_usd_provider was injected. The fix threads a companion
#   free_cash_usd_provider; a live free-cash authority that returns None is a typed
#   transient fault (BANKROLL_FREE_CASH_MISSING), never a silent unclamped submit.
"""FINDING-B relationship invariant: when the bankroll basis comes from an injected
provider AND a companion free-cash authority is wired, the chosen stake is bounded by
free cash (min, applied once); a free-cash authority that cannot resolve fails CLOSED
with a typed TRANSIENT reason rather than sizing unclamped.

These reuse the full receipt-sizing harness from test_event_reactor_no_bypass (the same
fixtures that exercise the live Kelly path), so the assertions pin the END-TO-END
relationship (provider in -> bounded stake / typed fault out), not a unit shim.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from src.events.reactor import _is_transient_money_path_reason

# Reuse the proven receipt-sizing harness + fixtures.
from tests.engine.test_event_reactor_no_bypass import (  # noqa: E402
    DECISION_TIME,
    _insert_replacement_forecast_fixture,
    _receipt,
    _trade_conn_with_snapshot,
)


def _current_trade_conn(*, written_at=None):
    # First INSERT clock for this synthetic connection, after source availability
    # but before the fixed posterior/decision cuts; never UPDATE sealed clocks.
    written_at = written_at or DECISION_TIME - timedelta(minutes=1, seconds=1)
    owning_connect = sqlite3.connect
    private = []
    def connect(database, *args, **kwargs):
        conn = owning_connect(database, *args, **kwargs)
        if database == ":memory:" and not private:
            conn.create_function("current_timestamp", 0,
                                 lambda: written_at.strftime("%Y-%m-%d %H:%M:%S"))
            private.append(conn)
        return conn
    # Source-run/coverage are inserted inside this constructor, before the
    # replacement posterior helper. Restore connect immediately afterwards.
    with pytest.MonkeyPatch.context() as clock:
        clock.setattr(sqlite3, "connect", connect)
        conn = _trade_conn_with_snapshot()
    assert private == [conn]
    _insert_replacement_forecast_fixture(conn)
    conn.execute("ALTER TABLE ensemble_snapshots ADD COLUMN provenance_json TEXT")
    # The minimal receipt fixture omitted the exact consumed snapshot binding.
    # Bind its existing dependency, without changing source clocks or expiry.
    row = conn.execute("SELECT posterior_id,dependency_source_run_ids_json,provenance_json "
                       "FROM forecast_posteriors WHERE posterior_id=9001").fetchone()
    provenance = json.loads(row["provenance_json"])
    dependency = json.loads(row["dependency_source_run_ids_json"])
    provenance["bayes_precision_fusion"]["current_evidence_shape"]["snapshot_id"] = dependency["current_ensemble_snapshot"]
    conn.execute("UPDATE forecast_posteriors SET provenance_json=? WHERE posterior_id=?",
                 (json.dumps(provenance), row["posterior_id"]))
    return conn


@pytest.fixture
def _legacy_fixture_clock(monkeypatch):
    """Only the old malformed-identity controls use their declared May cut."""
    from src.data import replacement_forecast_bundle_reader as reader
    owning_datetime = reader.datetime
    class ClockType(type):
        def __instancecheck__(cls, value):
            return isinstance(value, owning_datetime)
    class DecisionClock(owning_datetime, metaclass=ClockType):
        @classmethod
        def now(cls, tz=None):
            return DECISION_TIME.astimezone(tz) if tz else DECISION_TIME.replace(tzinfo=None)
    monkeypatch.setattr(reader, "datetime", DecisionClock)


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


@pytest.mark.parametrize("wrong_token", (False, True), ids=("exact_tokens", "wrong_token"))
def test_cash_book_capture_preserves_bound_token_identity(wrong_token):
    """Book-only contract control, not a source or probability qualification."""
    from tests.integration import test_w3_solve_seam_g3 as books
    probability = books._current_global_book_probability()
    at = books._dt.datetime(2026, 6, 13, 8, tzinfo=books._dt.timezone.utc)
    with closing(books._global_book_metadata_conn(probability)) as trade:
        responses = {token: _cash_book_response(token, binding.condition_id,
            {"asks": [{"price": "0.30", "size": "100"}],
             "bids": [{"price": "0.20", "size": "100"}]}, "0.01", "5")
            for binding in probability.bindings
            for token in (binding.yes_token_id, binding.no_token_id)}
        first = next(iter(responses))
        if wrong_token:
            responses[first]["asset_id"] = "foreign-token"
        def capture():
            return books.capture_current_global_book_epoch(
                trade, probability_witnesses={probability.family_key: probability},
                get_books=lambda tokens: {token: responses[token] for token in tokens},
                clock=lambda: at, max_age=timedelta(seconds=30))
        if wrong_token:
            with pytest.raises(ValueError, match="GLOBAL_BOOK_TOKEN_MISMATCH"):
                capture()
        else:
            epoch = capture()
            assert len(epoch.assets) == len(epoch.sell_assets) == 2 * len(probability.bindings)
            assert {asset.token_id for asset in epoch.assets} == set(responses)
            assert all(asset.curve.book_hash == books.universe._canonical_raw_book_hash(responses[asset.token_id])
                       for asset in epoch.assets)


def _normal_cash_matrix(*, conn, city, request, bundle, decision_time, monkeypatch,
                        book_inputs=None):
    """The normal committed FSR and immutable London q feed the real receipt."""
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
    from src.solve.solver import CurrentFamilyProbabilityAuthority
    from src.state import collateral_ledger as collateral
    from src.state.portfolio import load_runtime_open_portfolio
    from src.engine.global_batch_runtime import (
        _bind_selection_holdings, _store_global_auction_receipt, _bind_stored_global_auction_receipt,
    )
    from tests.fakes.polymarket_v2 import FakeClock, FakeCollateralLedger, FakePolymarketVenue
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
        # Full-family asks sum to one; the central YES offers positive edge.
        for index, market in enumerate(markets):
            yes = market["token_id"]
            no = f"private-london-no-{index}"
            yes_ask = "0.10" if index == 1 else "0.45"
            no_ask = "0.90" if index == 1 else "0.55"
            depth = json.dumps({side: {
                "asks": [{"price": ask, "size": "1000"}],
                "bids": [{"price": "0.05", "size": "1000"}]}
                for side, ask in (("YES", yes_ask), ("NO", no_ask))})
            for side, token, ask in (("YES", yes, yes_ask), ("NO", no, no_ask)):
                row = dict(snapshot_id=f"cash-{index}-{side}", gamma_market_id=f"cash-market-{index}",
                    event_id="private-london-2026-10-04", event_slug="private-london-high",
                    condition_id=market["condition_id"], question_id=f"cash-question-{index}",
                    yes_token_id=yes, no_token_id=no, selected_outcome_token_id=token,
                    outcome_label=side, enable_orderbook=1, active=1, closed=0, accepting_orders=1,
                    min_tick_size="0.01", min_order_size="5", fee_details_json='{"fee_rate_fraction":0}',
                    token_map_json=json.dumps({"yes": yes, "no": no}), neg_risk=0,
                    orderbook_top_bid="0.05", orderbook_top_ask=ask, orderbook_depth_json=depth,
                    raw_gamma_payload_hash="a"*64, raw_clob_market_info_hash="b"*64,
                    raw_orderbook_hash="c"*64, authority_tier="CLOB",
                    captured_at=decision_time.isoformat(),
                    freshness_deadline=(decision_time+timedelta(seconds=180)).isoformat())
                row.update(market_start_at=None, market_end_at=None, market_close_at=None,
                           sports_start_at=None, rfqe=None)
                row["fee_details"] = json.loads(row.pop("fee_details_json"))
                row["token_map_raw"] = json.loads(row.pop("token_map_json"))
                row["orderbook_depth_jsonb"] = row.pop("orderbook_depth_json")
                if book_inputs is not None:
                    # Change controlled provider input before the append-only
                    # canonical capture, never UPDATE a sealed snapshot.
                    book_inputs(snapshot=row, event=event, markets=markets,
                                decision_time=decision_time)
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
        if book_inputs is not None and preparation.prepared_global_family is None:
            return {"preparation": preparation}
        assert preparation.prepared_global_family is not None, preparation.reason
        prepared = preparation.prepared_global_family
        tokens = {market["condition_id"]: (market["token_id"], f"private-london-no-{index}")
                  for index, market in enumerate(markets)}
        probability = universe._rebind_probability_witness_tokens(
            prepared.probability_witness, token_map_by_condition=tokens,
            required_token_ids=frozenset(token for pair in tokens.values() for token in pair))
        prepared = replace(prepared, probability_witness=probability)
        assert list(probability.yes_point_q) == pytest.approx(list(bundle.q.values()))
        books = _cash_raw_books(trade.execute("SELECT * FROM executable_market_snapshots"))
        try:
            epoch = universe.capture_current_global_book_epoch(
                trade, probability_witnesses={probability.family_key: probability},
                get_books=lambda token_ids, **kwargs: {token: books[token] for token in token_ids},
                clock=lambda: decision_time, max_age=timedelta(seconds=180))
        except ValueError as exc:
            if book_inputs is None:
                raise
            return {"book_unavailable_reason": str(exc)}
        from src.data import replacement_forecast_bundle_reader as reader
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
            # independently selects the lawful immediate-taker proposal.
            selected = select_prepared_global_auction(
                prepared_by_event, selection_epoch_identity=f"cash-{name}",
                selection_cut_at_utc=decision_time, current_scope=scope,
                current_scope_identity_resolver=lambda: scope.scope_identity,
                venue_universe_identity=epoch.witness_identity,
                current_venue_universe_identity_resolver=lambda: epoch.witness_identity,
                universe_max_age=timedelta(seconds=180),
                current_probability_resolver=lambda _: CurrentFamilyProbabilityAuthority.from_witness(probability),
                current_execution_resolver=lambda candidate: epoch.execution_authority(candidate, checked_at_utc=decision_time),
                current_wealth_identity_resolver=lambda: wealth.economic_identity,
                wealth_witness=wealth, capital_limit_usd=Decimal("5" if name == "small" else "1000"),
                fractional_kelly_multiplier=Decimal(str(adapter._runtime_kelly_multiplier())),
                decision_at_utc=decision_time, book_epoch=epoch)
            if book_inputs is not None and selected.actuation is None:
                return {"selection": selected}
            assert selected.actuation is not None, selected.decision.no_trade_reason
            assert selected.decision.candidate.execution_mode == "TAKER_LIMIT"
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
            receipts[name] = _receipt(event, trade, forecast_conn=conn, topology_conn=conn,
                calibration_conn=conn, decision_time=decision_time,
                bankroll_usd_provider=lambda: 1000., reserve_on_pass=False,
                global_actuation=selected.actuation, **extra)
        return receipts


@pytest.fixture(scope="module")
def cash_matrix():
    """One actual normal producer; each cash case consumes the same sealed q."""
    from contextlib import ExitStack
    from pathlib import Path
    import tempfile
    from functools import partial
    from src import config
    from tests import test_replacement_forecast_materializer as normal
    root = Path(tempfile.mkdtemp(prefix="cash-normal-full-y-", dir=config.STATE_DIR))
    with pytest.MonkeyPatch.context() as patch, ExitStack() as stack:
        patch.setattr(config, "STATE_DIR", root)
        patch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
        surfaces = normal._hko_native_surfaces.__wrapped__(root, patch)
        next(surfaces)
        stack.callback(lambda: next(surfaces, None))
        source = normal._hko_source_surface.__wrapped__(root, patch, None)
        next(source)
        stack.callback(lambda: next(source, None))
        return normal._normal_native_originals_public_case(
            root, patch, "high", full_y_ready=partial(_normal_cash_matrix, monkeypatch=patch))


def test_free_cash_fixture_missing_identity_remains_rejected(_legacy_fixture_clock):
    from src.data.replacement_forecast_bundle_reader import _current_ensemble_snapshot_identity_reason
    conn = _current_trade_conn()
    row = conn.execute("SELECT dependency_source_run_ids_json,provenance_json "
                       "FROM forecast_posteriors WHERE posterior_id=9001").fetchone()
    provenance = json.loads(row["provenance_json"])
    del provenance["bayes_precision_fusion"]["current_evidence_shape"]["snapshot_id"]
    try:
        assert _current_ensemble_snapshot_identity_reason(
            conn, dependency_json=json.loads(row["dependency_source_run_ids_json"]),
            provenance=provenance, city="Chicago", target_date="2026-05-25", metric="high",
        ) == "REPLACEMENT_CURRENT_COORDINATE_IDENTITY_MISMATCH"
    finally:
        conn.close()


def test_free_cash_fixture_expired_coverage_remains_rejected(monkeypatch, _legacy_fixture_clock):
    from src.data import replacement_forecast_bundle_reader as reader
    conn = _current_trade_conn()
    row = conn.execute("SELECT dependency_source_run_ids_json,provenance_json "
                       "FROM forecast_posteriors WHERE posterior_id=9001").fetchone()
    expires = conn.execute("SELECT expires_at FROM source_run_coverage WHERE coverage_id='coverage-1'").fetchone()[0]
    clock = reader.datetime
    expired_at = clock.fromisoformat(expires) + timedelta(seconds=1)
    class ExpiredClock(clock):
        @classmethod
        def now(cls, tz=None):
            return expired_at.astimezone(tz) if tz else expired_at.replace(tzinfo=None)
    monkeypatch.setattr(reader, "datetime", ExpiredClock)
    try:
        assert reader._current_ensemble_snapshot_identity_reason(
            conn, dependency_json=json.loads(row["dependency_source_run_ids_json"]),
            provenance=json.loads(row["provenance_json"]), city="Chicago",
            target_date="2026-05-25", metric="high",
        ) == "REPLACEMENT_CURRENT_ENSEMBLE_SNAPSHOT_COVERAGE_BLOCKED"
        assert conn.execute("SELECT expires_at FROM source_run_coverage WHERE coverage_id='coverage-1'").fetchone()[0] == expires
    finally:
        conn.close()


def test_free_cash_fixture_future_written_coverage_remains_rejected(_legacy_fixture_clock):
    from src.data.replacement_forecast_bundle_reader import _current_ensemble_snapshot_identity_reason
    future = DECISION_TIME + timedelta(seconds=1)
    conn = _current_trade_conn(written_at=future)
    row = conn.execute("SELECT dependency_source_run_ids_json,provenance_json "
                       "FROM forecast_posteriors WHERE posterior_id=9001").fetchone()
    try:
        assert _current_ensemble_snapshot_identity_reason(
            conn, dependency_json=json.loads(row["dependency_source_run_ids_json"]),
            provenance=json.loads(row["provenance_json"]), city="Chicago",
            target_date="2026-05-25", metric="high",
        ) == "REPLACEMENT_CURRENT_ENSEMBLE_SNAPSHOT_COVERAGE_BLOCKED"
        assert conn.execute("SELECT recorded_at FROM source_run_coverage WHERE coverage_id='coverage-1'").fetchone()[0] == future.strftime("%Y-%m-%d %H:%M:%S")
    finally:
        conn.close()


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
