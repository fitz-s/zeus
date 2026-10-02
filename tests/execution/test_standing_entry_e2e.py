# Created: 2026-10-02
# Last reused/audited: 2026-10-02
# Authority basis: standing ENTRY keep-by-value law (operator, 2026-09-30) and its
#   re-review (2026-10-02): C3 must protectively cancel a rest whose probability
#   authority becomes INTRINSICALLY invalid, through the real current-probability
#   path, not a raising stub.
"""C3 end to end on the real probability path: KEEP, then intrinsic invalidity.

Real: the replacement-forecast seed (KORD low, Chicago), the current global
scope scan, ``_prepare_current_global_probability_family`` (its bundle reader,
readiness and raw-input re-verification), the trade ledger, the wealth
witness and the risk allocator. Phase 1 keeps the validated rest. Phase 2
breaks the consumed anchor artifact's recorded digest, an intrinsic
invalidity (the consumed raw input no longer verifies), not "a newer cycle
exists", so the verdict holds under both raw-input-HWM laws.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import src.execution.staleness_cancel as C
from tests.integration.test_w3_solve_seam_g3 import (  # noqa: F401 - pytest fixture
    _kord_normal_prior_fixture,
    _noaa_native_sources,
)

UTC = timezone.utc
FAMILY = ("Chicago", "2026-10-02", "low")
CONDITION = "0x" + f"{103:064x}"
TOKEN = "kord-yes-2"
NO_TOKEN = "kord-no-2"


def _trade_db():
    from src.state.db import init_schema, init_schema_trade_only

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_trade_only(conn)
    return conn


def _seed_rest_ledger(conn, *, at):
    """A 10 @ 0.50 YES rest on the KORD bin q=1 favours, its ledger and cash."""
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.entry_exposure_obligation import open_entry_exposure_obligation
    from src.state.schema.entry_exposure_obligations_schema import ensure_table
    from src.state.snapshot_repo import insert_snapshot
    from src.state.venue_command_repo import insert_submission_envelope

    init_collateral_schema(conn)
    ensure_table(conn)
    created = at - timedelta(hours=3)
    # Every KORD bin has a persisted executable snapshot (its YES/NO pair), as
    # live does; the rest sits on the third bin's YES token (inserted below).
    for index in (0, 1):
        insert_snapshot(
            conn,
            ExecutableMarketSnapshot(
                snapshot_id=f"snap-kord-{index}", gamma_market_id=f"gamma-kord-{index}",
                event_id="event-kord", event_slug="kord",
                condition_id="0x" + f"{101 + index:064x}", question_id=f"q-kord-{index}",
                yes_token_id=f"kord-yes-{index}", no_token_id=f"kord-no-{index}",
                selected_outcome_token_id=f"kord-yes-{index}", outcome_label="YES",
                enable_orderbook=True, active=True, closed=False, accepting_orders=True,
                market_start_at=None, market_end_at=None, market_close_at=None,
                sports_start_at=None, min_tick_size=D("0.01"), min_order_size=D("5"),
                fee_details={"bps": 0, "builder_fee_bps": 0},
                token_map_raw={"YES": f"kord-yes-{index}", "NO": f"kord-no-{index}"},
                rfqe=None, neg_risk=False, orderbook_top_bid=D("0.01"),
                orderbook_top_ask=D("0.02"), orderbook_depth_jsonb="{}",
                raw_gamma_payload_hash="a" * 64, raw_clob_market_info_hash="b" * 64,
                raw_orderbook_hash="c" * 64, authority_tier="CLOB",
                captured_at=created, freshness_deadline=created + timedelta(days=365),
            ),
        )
    insert_snapshot(
        conn,
        ExecutableMarketSnapshot(
            snapshot_id="snap-kord", gamma_market_id="gamma-kord", event_id="event-kord",
            event_slug="kord", condition_id=CONDITION, question_id="q-kord",
            yes_token_id=TOKEN, no_token_id=NO_TOKEN, selected_outcome_token_id=TOKEN,
            outcome_label="YES", enable_orderbook=True, active=True, closed=False,
            accepting_orders=True, market_start_at=None, market_end_at=None,
            market_close_at=None, sports_start_at=None, min_tick_size=D("0.01"),
            min_order_size=D("5"), fee_details={"bps": 0, "builder_fee_bps": 0},
            token_map_raw={"YES": TOKEN, "NO": NO_TOKEN}, rfqe=None, neg_risk=False,
            orderbook_top_bid=D("0.49"), orderbook_top_ask=D("0.56"),
            orderbook_depth_jsonb=json.dumps({
                "asset_id": TOKEN,
                "asks": [{"price": "0.56", "size": "500"}],
                "bids": [{"price": "0.49", "size": "500"}],
            }),
            raw_gamma_payload_hash="a" * 64, raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64, authority_tier="CLOB",
            captured_at=created, freshness_deadline=created + timedelta(days=365),
        ),
    )
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2", sdk_version="test", host="https://clob-v2.polymarket.com",
            chain_id=137, funder_address="0xfunder", condition_id=CONDITION, question_id="q-kord",
            yes_token_id=TOKEN, no_token_id=NO_TOKEN, selected_outcome_token_id=TOKEN,
            outcome_label="YES", side="BUY", price=D("0.50"), size=D("10"), order_type="GTC",
            post_only=True, tick_size=D("0.01"), min_order_size=D("5"), neg_risk=False,
            fee_details={"source": "test", "token_id": TOKEN, "fee_rate_fraction": 0.0,
                         "fee_rate_bps": 0.0, "fee_rate_source_field": "fee_rate_fraction",
                         "fee_rate_raw_unit": "fraction"},
            canonical_pre_sign_payload_hash="a" * 64, signed_order=None, signed_order_hash=None,
            raw_request_hash="b" * 64, raw_response_json=None, order_id=None, trade_ids=(),
            transaction_hashes=(), error_code=None, error_message=None,
            captured_at=created.isoformat(),
        ),
        envelope_id="env-kord",
    )
    conn.execute(
        """INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id, idempotency_key,
            intent_kind, market_id, token_id, side, size, price, venue_order_id, state,
            created_at, updated_at, q_version
        ) VALUES ('cmd', 'snap-kord', 'env-kord', 'pos-cmd', 'decision-cmd', ?, 'ENTRY',
                  ?, ?, 'BUY', 10.0, 0.50, 'venue-1', 'ACKED', ?, ?, 'q-submitted')""",
        ("cmd".ljust(32, "0"), CONDITION, TOKEN, created.isoformat(), created.isoformat()),
    )
    conn.execute(
        "INSERT INTO venue_order_facts (venue_order_id, command_id, state, remaining_size, "
        "matched_size, source, observed_at, local_sequence, raw_payload_hash) "
        "VALUES ('venue-1', 'cmd', 'LIVE', '10', '0', 'REST', ?, 0, ?)",
        (created.isoformat(), "f" * 64),
    )
    conn.execute(
        "INSERT INTO collateral_reservations (command_id, reservation_type, amount, created_at) "
        "VALUES ('cmd', 'PUSD_BUY', 5000000, ?)",
        (created.isoformat(),),
    )
    open_entry_exposure_obligation(
        conn, command_id="cmd", owner_domain="test", token_id=TOKEN,
        condition_id=CONDITION, shares=10.0, cost_basis_usd=5.0,
    )
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots ("
        "pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,"
        "captured_at,authority_tier,raw_balance_payload_hash"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        (105_000_000, 10**12, 0, "{}", "{}", 5_000_000, "{}",
         (at - timedelta(seconds=5)).isoformat(), "CHAIN", "h"),
    )
    conn.commit()


def _publish_allocator(conn):
    from src.control.heartbeat_supervisor import HeartbeatHealth
    from src.risk_allocator import GovernorState, RiskAllocator, configure_global_allocator, load_cap_policy

    configure_global_allocator(
        RiskAllocator.from_position_lots(conn, load_cap_policy()),
        GovernorState(
            current_drawdown_pct=0.0, heartbeat_health=HeartbeatHealth.HEALTHY,
            ws_gap_active=False, ws_gap_seconds=0, unknown_side_effect_count=0,
            reconcile_finding_count=0,
        ),
    )


def _pin_reader_clock(monkeypatch, at):
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import event_reactor_adapter as adapter

    class _ClockType(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class _ReaderClock(datetime, metaclass=_ClockType):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)

    monkeypatch.setattr(reader, "datetime", _ReaderClock)
    monkeypatch.setattr(adapter, "_GLOBAL_PROBABILITY_FAMILY_CACHE", {})
    monkeypatch.setattr(adapter, "_GLOBAL_PROBABILITY_FAMILY_INELIGIBLE_CACHE", {})


class _Venue:
    def __init__(self):
        self.calls = []

    def cancel_orders_batch(self, ids):
        self.calls.append(list(ids))
        return [{"canceled": True, "orderID": i} for i in ids]


def test_c3_keeps_a_validated_rest_then_cancels_it_on_intrinsic_invalidity(
    tmp_path, monkeypatch, _noaa_native_sources,  # noqa: F811 - pytest fixture
):
    from src.engine import event_reactor_adapter as adapter
    from src.engine import global_batch_runtime as runtime
    from src.execution import day0_hard_fact_exit

    fx = _kord_normal_prior_fixture(tmp_path, monkeypatch, target_date=date(2026, 10, 2))
    try:
        at = fx.cut + timedelta(hours=1)
        _pin_reader_clock(monkeypatch, at)
        monkeypatch.setattr(adapter, "_runtime_kelly_multiplier", lambda: 0.125)
        monkeypatch.setattr(
            "src.runtime.bankroll_provider.current_zeus_capital_allocation_setting",
            lambda: {"mode": "wallet_total"},
        )
        # Calibration needs a canonical fill corpus this seed cannot carry;
        # the resolver's own unavailable branch is covered elsewhere.
        monkeypatch.setattr(
            runtime, "_market_anchored_correction_resolver",
            lambda *_a, **_k: (lambda *_c: None),
        )
        monkeypatch.setattr(
            day0_hard_fact_exit, "classify_day0_dead_bin_entry_cancels", lambda *_a, **_k: [],
        )
        trade = _trade_db()
        _seed_rest_ledger(trade, at=at)
        _publish_allocator(trade)
        world = sqlite3.connect(":memory:")

        families = C.resolve_order_families(C.find_open_entry_rests(trade), trade, fx.conn)
        assert families == {"cmd": FAMILY}

        venue = _Venue()
        kept = C.run_c3_staleness_cancel_cycle(
            trade, trade, fx.conn, venue, world_conn_ro=world, clock=lambda: at,
        )
        valuation = kept["valuations"][0]
        assert valuation.action == "KEEP", valuation.reason
        assert valuation.evidence["authority_valid"] is True
        assert venue.calls == []

        # Intrinsic invalidity: the consumed anchor artifact no longer verifies.
        fx.conn.execute(
            "UPDATE raw_forecast_artifacts SET sha256=? WHERE artifact_id=2", ("0" * 64,)
        )
        fx.conn.commit()
        monkeypatch.setattr(adapter, "_GLOBAL_PROBABILITY_FAMILY_CACHE", {})
        monkeypatch.setattr(adapter, "_GLOBAL_PROBABILITY_FAMILY_INELIGIBLE_CACHE", {})

        cancelled = C.run_c3_staleness_cancel_cycle(
            trade, trade, fx.conn, venue, world_conn_ro=world, clock=lambda: at,
        )
        valuation = cancelled["valuations"][0]
        assert valuation.action == "CANCEL"
        assert valuation.evidence["authority_valid"] is False
        assert valuation.reason.startswith("ENTRY_REST_PROBABILITY_BLOCKED:ValueError:")
        assert "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE" in valuation.reason
        assert venue.calls == [["venue-1"]]
        assert trade.execute(
            "SELECT state FROM venue_commands WHERE command_id='cmd'"
        ).fetchone()[0] == "CANCELLED"
    finally:
        fx.conn.close()
        fx.builtin.close()
