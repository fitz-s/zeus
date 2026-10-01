# Created: 2026-10-01
# Last reused/audited: 2026-10-01
# Authority basis: standing ENTRY keep-by-value law (operator, 2026-09-30): an open ENTRY
#   rest keeps working toward its current fractional-Kelly target R* (the selector's own
#   mean-q sizer at the rest's limit); a posterior identity change only triggers
#   revaluation; ENTRY rests have no age deadline.
"""Standing ENTRY valuation: R* is the selector's own BUY sizer at the rest's limit,
and the C3 cycle keeps or cancels from it."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace

import numpy as np
import pytest

import src.execution.staleness_cancel as C
from src.contracts.strategy_capital_allocation import StrategyCapitalAllocationWitness
from src.solve import solver as S

UTC = timezone.utc
NOW = datetime(2026, 7, 10, 6, 0, tzinfo=UTC)
FAMILY = ("Miami", "2026-07-12", "high")
FAMILY_KEY = "Miami|2026-07-12|high"
TOKEN = "tok-rest"
CONDITION = "cond-rest"


@pytest.fixture(autouse=True)
def _dry_run(monkeypatch):
    monkeypatch.delenv("ZEUS_ENTRY_Q_VERSION_STRICT", raising=False)
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    monkeypatch.setenv("ZEUS_MODE", "dry_run")


def _witness(*, q: float, posterior: str = "posterior-a", side: str = "YES") -> S.JointOutcomeProbabilityWitness:
    """A two-bin family; the rest's bin carries posterior-mean YES q."""
    bindings = (
        S.OutcomeTokenBinding(
            bin_id="bin-rest",
            condition_id=CONDITION,
            yes_token_id=TOKEN if side == "YES" else "yes-rest",
            no_token_id=TOKEN if side == "NO" else "no-rest",
        ),
        S.OutcomeTokenBinding(
            bin_id="bin-other", condition_id="cond-other",
            yes_token_id="yes-other", no_token_id="no-other",
        ),
    )
    yes = q if side == "YES" else 1.0 - q
    samples = np.column_stack((np.full(400, yes), np.full(400, 1.0 - yes)))
    fields = dict(
        family_key=FAMILY_KEY,
        bindings=bindings,
        q_version=f"q-{posterior}",
        resolution_identity="resolution",
        topology_identity="topology",
        posterior_identity_hash=posterior,
        source_truth_identity="source",
        authority_certificate_hash=f"certificate-{posterior}",
        band_alpha=0.05,
        band_basis="joint_q_band_samples",
        yes_point_q=np.mean(samples, axis=0),
        yes_q_samples=samples,
        captured_at_utc=NOW,
    )
    return S.JointOutcomeProbabilityWitness(
        **fields,
        max_age=timedelta(minutes=3),
        witness_identity=S.joint_probability_witness_identity(**fields),
    )


RESOLUTION_AT = NOW + timedelta(hours=36)


def _obligation_row(*, command_id="cmd", shares="10", cost="5", status="OPEN", position_id="pos-cmd"):
    """One ``entry_obligation_rows`` row: (command_id, status, token, shares, cost,
    unbounded, created_at, position_id, command token, side, size, price,
    intent_kind, state, fill_confirmed_at, fixed_cash_fak)."""
    return (
        command_id, status, TOKEN, shares, cost, 0, NOW.isoformat(),
        position_id, TOKEN, "BUY", shares, str(D(cost) / D(shares)), "ENTRY", "ACKED", None, 0,
    )


def _wealth(
    *,
    cash: str = "100",
    reservation: str = "5",
    rows=None,
    positions=(),
    native: dict | None = None,
    extra_commitment_micro: int = 0,
) -> S.PortfolioWealthWitness:
    """The witness ``current_portfolio_wealth_witness`` builds over ``rows``:
    $``cash`` spendable, the rest's $``reservation`` held back, its obligation
    pending and costed, and any held native shares committed."""
    from src.engine.global_auction_universe import pending_entry_endowments_from_rows

    rows = [_obligation_row()] if rows is None else rows
    native = dict(native or {})
    pending, _ids, cost, _oids = pending_entry_endowments_from_rows(
        rows, positions=tuple(positions), native_holdings_micro=native
    )
    commitments = dict(cost and {TOKEN: sum(cost.values())} or {})
    if extra_commitment_micro:
        commitments[TOKEN] = commitments.get(TOKEN, 0) + extra_commitment_micro
    committed = sum((D(v) / D(1_000_000) for v in commitments.values()), D("0"))
    allocation = StrategyCapitalAllocationWitness.build(
        capital_basis_usd=D(cash) + committed,
        committed_capital_usd=committed,
        venue_spendable_cash_usd=D(cash),
        allocation={"mode": "wallet_total"},
    )
    held = sum((D(v) for v in native.values()), D("0")) / D(1_000_000)
    pend = sum((D(r[2]) for r in pending), D("0")) / D(1_000_000)
    fields = dict(
        ledger_snapshot_id="ledger-current",
        position_set_hash="positions",
        wealth_floor_usd=D(cash),
        wealth_ceiling_usd=D(cash) + held + pend,
        spendable_cash_usd=D(cash),
        reservations_usd=D(reservation),
        collateral_authority="CHAIN",
        captured_at_utc=NOW,
    )
    return S.PortfolioWealthWitness(
        **fields,
        strategy_capital_allocation=allocation,
        max_age=timedelta(minutes=3),
        witness_identity=S.portfolio_wealth_identity(
            **fields, strategy_capital_allocation_identity=allocation.witness_identity,
        ),
        native_holdings_micro=tuple(sorted(native.items())),
        pending_entry_endowments_micro=tuple(sorted(pending)),
        native_commitments_micro=tuple(sorted((t, a) for t, a in commitments.items() if a)),
    )


def _own(*, size="10", filled="0", price="0.50", at_risk_micro=5_000_000, position=None):
    return C.OwnCommandCapital(
        command_id="cmd",
        token_id=TOKEN,
        size=D(size),
        price=D(price),
        filled_shares=D(filled),
        at_risk_micro=at_risk_micro,
        position=position,
    )


def _own_view(wealth=None, own=None, *, rows=None, positions=(), native=None):
    return C._own_reservation_wealth(
        wealth if wealth is not None else _wealth(rows=rows, positions=positions, native=native),
        own if own is not None else _own(),
        obligation_rows=[_obligation_row()] if rows is None else rows,
        positions=tuple(positions),
        native_holdings_micro=dict(native or {}),
    )


def _snapshot(*, min_order: str = "5") -> dict:
    depth = {"asks": [{"price": "0.56", "size": "500"}], "bids": [{"price": "0.49", "size": "500"}]}
    return {
        "snapshot_id": "snap-rest",
        "condition_id": CONDITION,
        "yes_token_id": TOKEN,
        "no_token_id": "no-rest",
        "selected_outcome_token_id": TOKEN,
        "min_tick_size": "0.01",
        "min_order_size": min_order,
        "neg_risk": 0,
        "fee_details_json": json.dumps({"fee_rate_fraction": 0.0, "token_id": TOKEN}),
        "orderbook_depth_json": json.dumps({"asset_id": TOKEN, **depth}),
        "raw_orderbook_hash": "c" * 64,
        "captured_at": NOW.isoformat(),
        "freshness_deadline": (NOW + timedelta(minutes=3)).isoformat(),
        "gamma_market_id": "gamma",
        "event_id": "event",
    }


def _rest(*, size: str = "10", matched: str = "0", price: str = "0.50") -> dict:
    return {
        "command_id": "cmd",
        "venue_order_id": "venue-1",
        "token_id": TOKEN,
        "snapshot_id": "snap-rest",
        "size": size,
        "price": price,
        "matched_size": matched,
        "q_version": "q-submitted",
    }


def _holdings(witness, wealth, *, positions=()):
    from src.engine.global_batch_runtime import _bind_selection_holdings
    from src.engine.qkernel_spine_bridge import PreparedGlobalFamily

    prepared = PreparedGlobalFamily(
        decision_id="d", probability_witness=witness, candidate_seeds=()
    )
    return _bind_selection_holdings(
        {"e": prepared}, portfolio_state=SimpleNamespace(positions=tuple(positions)),
        wealth_witness=wealth,
    )["e"].holdings_snapshot


def _prepared(witness, **fields):
    from src.engine.qkernel_spine_bridge import PreparedGlobalFamily

    return PreparedGlobalFamily(decision_id="d", probability_witness=witness, candidate_seeds=(), **fields)


def _value(*, q=0.75, posterior="posterior-a", cash="100", multiplier="0.125",
           capital_limit="100", size="10", matched="0", price="0.50", prepared_fields=None,
           resolution_at=RESOLUTION_AT):
    witness = _witness(q=q, posterior=posterior)
    rows = [_obligation_row(shares=size, cost=str(D(size) * D(price)))]
    own = C._own_reservation_wealth(
        _wealth(cash=cash, reservation=str(D(size) * D(price)), rows=rows),
        _own(size=size, filled=matched, price=price,
             at_risk_micro=int(D(size) * D(price) * 1_000_000)),
        obligation_rows=rows,
        positions=(),
        native_holdings_micro={},
    )
    return C.value_standing_entry(
        _rest(size=size, matched=matched, price=price),
        family=FAMILY,
        snapshot=_snapshot(),
        prepared=_prepared(witness, **(prepared_fields or {})),
        wealth=own,
        holdings_snapshot=_holdings(witness, own),
        fractional_kelly_multiplier=D(multiplier),
        capital_limit_usd=D(capital_limit),
        payoff_q_correction_resolver=None,
        resolution_at=resolution_at,
        now=NOW,
    )


class TestRStarIsTheSelectorsOwnSizer:
    def test_target_equals_selector_buy_sizer_at_the_rest_limit(self):
        value = _value()

        witness = _witness(q=0.75)
        own = _own_view()
        candidate = C._rest_candidate(
            _rest(), snapshot=_snapshot(), binding=witness.bindings[0], side="YES",
            probability_witness=witness, capacity=S.maker_buy_capacity(own.spendable_cash_usd, D("0.50")),
            ledger_snapshot_id=own.ledger_snapshot_id, now=NOW,
        )
        expected = S._score_global_single_order_buy_expected(
            candidate,
            payoff_probability_mean=0.75,
            sample_count=400,
            band_alpha=0.05,
            wealth_floor_usd=own.strategy_capital_allocation.utility_liquid_cash_usd,
            wealth_ceiling_usd=own.strategy_capital_allocation.utility_liquid_cash_usd,
            spendable_cash_usd=own.spendable_cash_usd,
            capital_limit_usd=D("100"),
            fractional_kelly_multiplier=D("0.125"),
            current_token_shares=D("0"),
        )
        assert expected.candidate is not None
        assert D(value.evidence["target_remaining"]) == expected.shares
        assert D(value.evidence["fractional_kelly_target_shares"]) == (
            expected.fractional_kelly_target_shares
        )
        assert value.evidence["conditional_gain"] == pytest.approx(
            expected.expected_terminal_wealth.expected_delta_log_wealth
        )

    def test_sizer_is_called_not_reimplemented(self, monkeypatch):
        calls = []
        real = S._score_global_single_order_buy_expected

        def spy(candidate, **kwargs):
            calls.append((candidate.execution_mode, candidate.economic_cost_curve.levels[0].price, kwargs))
            return real(candidate, **kwargs)

        monkeypatch.setattr(S, "_score_global_single_order_buy_expected", spy)
        _value(multiplier="0.125")

        assert len(calls) == 1
        mode, limit, kwargs = calls[0]
        assert (mode, limit) == ("MAKER_REST", D("0.50"))
        assert kwargs["fractional_kelly_multiplier"] == D("0.125")
        assert kwargs["payoff_probability_mean"] == pytest.approx(0.75)

    def test_own_reservation_is_available_to_its_own_remainder(self):
        base = _wealth(cash="100", reservation="5")
        own = _own_view(base)

        assert own.spendable_cash_usd == D("105")
        assert own.reservations_usd == D("0")
        assert own.strategy_capital_allocation.committed_capital_usd == D("0")
        assert own.pending_entry_endowments_micro == ()
        assert own.economic_identity != base.economic_identity

    def test_unprojected_fill_stays_holding_and_its_cash_is_not_released(self):
        # 4 of 10 filled, no runtime projection carries the fill yet: the
        # filled 4 stay owned exposure; only the unfilled 6's cash returns.
        base = _wealth(cash="100", reservation="5")
        own = _own_view(base, _own(filled="4"))

        assert own.spendable_cash_usd == D("103")
        assert own.pending_entry_endowments_micro == (("cmd", TOKEN, 4_000_000),)
        assert own.native_commitments_micro == ((TOKEN, 2_000_000),)

    def test_partial_fill_on_native_holdings_is_counted_once(self):
        # The collateral snapshot already holds the 4 filled shares and the
        # runtime position carries them. The witness counts them there and
        # through the OPEN obligation's projection gap; the counterfactual
        # must add nothing on top: R* sees exactly the 4 held shares.
        native = {TOKEN: 4_000_000}
        position = SimpleNamespace(
            position_id="pos-cmd", trade_id="pos-cmd", direction="buy_yes", token_id=TOKEN,
            no_token_id="no-rest", condition_id=CONDITION, shares=4.0,
        )
        rows = [_obligation_row()]
        # While the order is open its full $5 reservation stays held; the
        # terminal conversion law spends $2 on the fill and releases $3.
        base = _wealth(cash="100", reservation="5", rows=rows, positions=(position,),
                       native=native, extra_commitment_micro=2_000_000)
        own = _own_view(base, _own(filled="4", position=position),
                        rows=rows, positions=(position,), native=native)

        assert own.pending_entry_endowments_micro == ()
        assert own.native_holdings_micro == ((TOKEN, 4_000_000),)
        # Held shares' cost stays committed; only the open obligation's cost leaves.
        assert own.native_commitments_micro == ((TOKEN, 2_000_000),)
        assert own.spendable_cash_usd == D("103")
        witness = _witness(q=0.75)
        bound = _holdings(witness, own, positions=(position,))
        candidate = C._rest_candidate(
            _rest(matched="4"), snapshot=_snapshot(), binding=witness.bindings[0], side="YES",
            probability_witness=witness, capacity=D("100"), ledger_snapshot_id=own.ledger_snapshot_id,
            now=NOW,
        )
        from src.engine.global_single_order_auction import _candidate_portfolio_endowment

        endowment = _candidate_portfolio_endowment(
            candidate, probability_witness=witness, holdings_snapshot=bound, wealth_witness=own,
        )
        assert endowment.current_token_shares == D("4")

    def test_rows_that_disagree_with_the_witness_refuse_the_view(self):
        base = _wealth(cash="100", reservation="5")
        with pytest.raises(ValueError, match="ENTRY_REST_OBLIGATION_ROWS_NOT_THE_WITNESS"):
            _own_view(base, rows=[_obligation_row(shares="12", cost="6")])


class TestDisposition:
    def test_positive_value_at_or_under_target_keeps(self):
        value = _value(q=0.75)
        assert value.action == "KEEP"
        assert value.evidence["authority_valid"] is True
        assert value.evidence["expected_growth"]["capital_lock_hours"] == pytest.approx(36.0)

    def test_target_more_than_a_lot_below_remainder_cancels(self):
        # No amend and no same-order re-post: a lot-sized reduction cancels and
        # the family's confirmed-cancel redecision sizes a fresh order.
        value = _value(q=0.75, size="60")
        target = D(value.evidence["target_remaining"])
        assert D("60") - target >= D("5")
        assert value.action == "CANCEL"
        assert value.reason == "CURRENT_FRACTIONAL_TARGET_REDUCED"

    def test_day0_saturated_certainty_is_refuted_like_the_selector(self):
        value = _value(
            q=1.0,
            prepared_fields={},
        )
        witness = _witness(q=1.0)
        refuted = _value(
            q=1.0,
            prepared_fields={
                "day0_saturated_statistical_sides": (("bin-rest", "YES"),),
                "day0_saturation_witness_identity": witness.witness_identity,
            },
        )
        assert value.reason != refuted.reason
        assert refuted.action == "CANCEL"
        assert refuted.reason == "ENTRY_REST_BUY_REFUTED:DAY0_STATISTICAL_CERTAINTY_UNSUPPORTED"

    def test_missing_capital_horizon_cancels_protectively(self):
        value = _value(q=0.75, resolution_at=None)
        assert value.action == "CANCEL"
        assert value.evidence["authority_valid"] is False
        assert value.reason == "ENTRY_REST_CAPITAL_HORIZON_INVALID:CAPITAL_HORIZON_AUTHORITY_MISSING"

    @pytest.mark.parametrize("q", [0.50, 0.30])
    def test_non_positive_value_at_the_limit_cancels(self, q):
        value = _value(q=q)
        assert value.action == "CANCEL"
        assert value.evidence["target_remaining"] == "0"

    def test_own_reservation_alone_keeps_funding_its_rest(self):
        # No free cash: the order's own reservation still funds its remainder.
        value = _value(cash="0")
        assert value.reason != "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT:MAKER_CASH_CAPACITY_BELOW_LOT"
        assert D(value.evidence["proposal_capacity_shares"]) >= D("5")

    def test_target_below_one_lot_cancels(self):
        # q=0.51 at 0.50: even full Kelly is below one 5-share lot.
        value = _value(q=0.51)
        assert value.evidence["target_remaining"] == "0"
        assert value.action == "CANCEL"
        assert value.reason.startswith("FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT")

    def test_selector_small_capital_lot_rule_applies_to_the_rest(self):
        # q=0.52: 1/8-Kelly (~1 share) is below one lot but full Kelly (~8.4)
        # admits one lot, exactly as the selector sizes a fresh order. A
        # 10-share rest is one lot above that target, so it resizes.
        value = _value(q=0.52)
        assert D(value.evidence["fractional_kelly_target_shares"]) < D("5")
        assert D(value.evidence["full_kelly_target_shares"]) >= D("5")
        assert value.evidence["target_remaining"] == "5"
        assert value.action == "CANCEL"
        assert value.reason == "CURRENT_FRACTIONAL_TARGET_REDUCED"

    def test_posterior_identity_alone_never_changes_the_disposition(self):
        before = _value(q=0.75, posterior="posterior-a")
        after = _value(q=0.75, posterior="posterior-b")

        assert before.action == after.action == "KEEP"
        assert before.evidence["target_remaining"] == after.evidence["target_remaining"]
        assert before.evidence["probability_witness_identity"] != (
            after.evidence["probability_witness_identity"]
        )

    def test_rest_age_is_not_an_input(self):
        import inspect

        assert "created_at" not in inspect.getsource(C.value_standing_entry)
        assert "created_at" not in inspect.getsource(C._capture_standing_entry_values)


# ---------------------------------------------------------------------------
# The required end-to-end trace: an open early ENTRY rest whose family
# posterior identity changes while q and the book are unchanged ends in KEEP
# with no venue call; the same rest with q dropped below the limit's value
# ends in CANCEL through the persisted batch journal.
# ---------------------------------------------------------------------------


def _trade_db():
    from src.state.db import init_schema, init_schema_trade_only

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_trade_only(conn)
    return conn


def _seed_early_rest(conn):
    """An already-acknowledged 36 h-early GTC BUY rest: 10 @ 0.50, $5 reserved."""
    from tests.execution.test_staleness_cancel import _seed_open_entry

    _seed_open_entry(
        conn, command_id="cmd", token_id=TOKEN, venue_order_id="venue-1",
        q_version="q-submitted", created_at=NOW - timedelta(hours=3),
    )
    conn.execute(
        "INSERT INTO collateral_reservations (command_id, reservation_type, amount, created_at) "
        "VALUES ('cmd', 'PUSD_BUY', 5000000, ?)",
        (NOW.isoformat(),),
    )
    conn.commit()


class TestStandingEntryTrace:
    def _cycle(self, monkeypatch, *, q, posterior):
        from src.engine import event_reactor_adapter as adapter
        from src.engine import global_auction_universe as universe
        from src.engine.qkernel_spine_bridge import PreparedGlobalFamily
        from src.execution import day0_hard_fact_exit
        from src.risk_allocator import governor
        from src.state import portfolio as portfolio_module

        conn = _trade_db()
        _seed_early_rest(conn)
        monkeypatch.setattr(C, "resolve_order_families", lambda *_a: {"cmd": FAMILY})
        monkeypatch.setattr(C, "_snapshot_row", lambda _c, _sid: _snapshot())
        event = SimpleNamespace(
            event_id="evt",
            payload_json=json.dumps({"city": FAMILY[0], "target_date": FAMILY[1], "metric": FAMILY[2]}),
        )
        monkeypatch.setattr(
            universe, "scan_current_global_auction_scope",
            lambda **_k: SimpleNamespace(
                events=(event,), resolution_at_by_family={FAMILY_KEY: RESOLUTION_AT}
            ),
        )
        witness = _witness(q=q, posterior=posterior)
        monkeypatch.setattr(
            adapter, "_prepare_current_global_probability_family",
            lambda *_a, **_k: PreparedGlobalFamily(
                decision_id="d", probability_witness=witness, candidate_seeds=()
            ),
        )
        monkeypatch.setattr(adapter, "_runtime_kelly_multiplier", lambda: 0.125)
        from src.engine import global_batch_runtime as runtime

        # Calibration is not under test here: the resolver applies no
        # correction, so the acting q is the served posterior-mean q.
        monkeypatch.setattr(
            runtime, "_market_anchored_correction_resolver",
            lambda *_a, **_k: (lambda *_c: None),
        )
        monkeypatch.setattr(
            portfolio_module, "load_runtime_open_portfolio",
            lambda _c: SimpleNamespace(positions=(), chain_only_facts=()),
        )
        rows = [_obligation_row()]
        monkeypatch.setattr(
            universe, "current_portfolio_wealth_witness", lambda *_a, **_k: _wealth(rows=rows)
        )
        monkeypatch.setattr(universe, "entry_obligation_rows", lambda _conn: rows)
        monkeypatch.setattr(
            governor, "snapshot_global_auction_capital_authority",
            lambda: SimpleNamespace(capacity_usd=lambda **_k: D("1000")),
        )
        monkeypatch.setattr(
            day0_hard_fact_exit, "classify_day0_dead_bin_entry_cancels", lambda *_a, **_k: []
        )

        class Venue:
            def __init__(self):
                self.calls = []

            def cancel_orders_batch(self, ids):
                state = conn.execute("SELECT state FROM venue_commands WHERE command_id='cmd'").fetchone()[0]
                self.calls.append((list(ids), state))
                return [{"canceled": True, "orderID": i} for i in ids]

        venue = Venue()
        result = C.run_c3_staleness_cancel_cycle(
            conn, conn, sqlite3.connect(":memory:"), venue,
            world_conn_ro=sqlite3.connect(":memory:"), now=NOW,
        )
        return conn, venue, result

    def test_identity_change_with_same_q_and_book_keeps_without_venue_call(self, monkeypatch):
        conn, venue, result = self._cycle(monkeypatch, q=0.75, posterior="posterior-NEW")

        assert venue.calls == []
        assert [v.action for v in result["valuations"]] == ["KEEP"]
        assert result["cancel_set_size"] == 0
        row = conn.execute(
            "SELECT state, venue_order_id, q_version FROM venue_commands WHERE command_id='cmd'"
        ).fetchone()
        assert tuple(row) == ("ACKED", "venue-1", "q-submitted")
        artifact = json.loads(conn.execute(
            "SELECT artifact_json FROM decision_log WHERE mode='standing_entry_revaluation'"
        ).fetchone()[0])
        assert artifact["action"] == "KEEP"
        assert artifact["venue_order_id"] == "venue-1"
        assert artifact["evidence"]["posterior_identity_hash"] == "posterior-NEW"
        assert artifact["evidence"]["submission_q_version"] == "q-submitted"

    def test_same_rest_with_q_below_its_limit_value_cancels(self, monkeypatch):
        conn, venue, result = self._cycle(monkeypatch, q=0.45, posterior="posterior-NEW")

        assert [v.action for v in result["valuations"]] == ["CANCEL"]
        # CANCEL_REQUESTED was journaled, with its reason, before the venue call.
        assert venue.calls == [(["venue-1"], "CANCEL_PENDING")]
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM venue_command_events "
            "WHERE command_id='cmd' AND event_type='CANCEL_REQUESTED'"
        ).fetchone()[0])
        assert payload["cancel_reason"] == result["valuations"][0].reason
        assert conn.execute(
            "SELECT state FROM venue_commands WHERE command_id='cmd'"
        ).fetchone()[0] == "CANCELLED"
        assert result["confirmed_families"] == {FAMILY}
        artifact = json.loads(conn.execute(
            "SELECT artifact_json FROM decision_log WHERE mode='standing_entry_revaluation'"
        ).fetchone()[0])
        assert artifact["action"] == "CANCEL"

def test_blocked_probability_authority_cancels_through_the_persisted_path(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.engine import global_auction_universe as universe
    from src.execution import day0_hard_fact_exit

    conn = _trade_db()
    _seed_early_rest(conn)
    monkeypatch.setattr(C, "resolve_order_families", lambda *_a: {"cmd": FAMILY})
    event = SimpleNamespace(
        event_id="evt",
        payload_json=json.dumps({"city": FAMILY[0], "target_date": FAMILY[1], "metric": FAMILY[2]}),
    )
    monkeypatch.setattr(
        universe, "scan_current_global_auction_scope",
        lambda **_k: SimpleNamespace(events=(event,), resolution_at_by_family={}),
    )

    def blocked(*_a, **_k):
        raise ValueError("GLOBAL_CURRENT_REPLACEMENT_BUNDLE_BLOCKED:REPLACEMENT_RAW_INPUT_HWM")

    monkeypatch.setattr(adapter, "_prepare_current_global_probability_family", blocked)
    monkeypatch.setattr(day0_hard_fact_exit, "classify_day0_dead_bin_entry_cancels", lambda *_a, **_k: [])
    calls = []

    class Venue:
        def cancel_orders_batch(self, ids):
            calls.append(list(ids))
            return [{"canceled": True, "orderID": i} for i in ids]

    result = C.run_c3_staleness_cancel_cycle(
        conn, conn, sqlite3.connect(":memory:"), Venue(),
        world_conn_ro=sqlite3.connect(":memory:"), now=NOW,
    )

    valuation = result["valuations"][0]
    assert valuation.action == "CANCEL"
    assert valuation.evidence["authority_valid"] is False
    assert valuation.reason.startswith("ENTRY_REST_PROBABILITY_BLOCKED")
    assert calls == [["venue-1"]]


def test_an_unreadable_order_cancels_that_order_and_values_the_rest(monkeypatch):
    from src.engine import global_auction_universe as universe
    from src.state import venue_command_repo

    real = venue_command_repo.get_command

    def flaky(conn, command_id):
        if command_id == "cmd-bad":
            raise sqlite3.OperationalError("disk I/O error")
        return real(conn, command_id)

    monkeypatch.setattr(venue_command_repo, "get_command", flaky)
    monkeypatch.setattr(
        universe, "scan_current_global_auction_scope",
        lambda **_k: (_ for _ in ()).throw(ValueError("no scope in this test")),
    )
    conn = _trade_db()
    _seed_early_rest(conn)
    entries = [
        {"command_id": "cmd-bad", "venue_order_id": "venue-bad", "token_id": "tok-bad"},
        {"command_id": "cmd", "venue_order_id": "venue-1", "token_id": TOKEN},
    ]

    values = C._capture_standing_entry_values(
        conn, sqlite3.connect(":memory:"), sqlite3.connect(":memory:"), entries,
        families={"cmd-bad": FAMILY, "cmd": FAMILY}, now=NOW,
    )

    assert [(v.command_id, v.action) for v in values] == [("cmd-bad", "CANCEL"), ("cmd", "CANCEL")]
    assert values[0].reason == "ENTRY_REST_COMMAND_UNREADABLE:OperationalError"
    assert values[1].reason.startswith("ENTRY_REST_CURRENT_SCOPE_UNAVAILABLE")
