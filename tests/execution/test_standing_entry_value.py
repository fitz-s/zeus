# Created: 2026-10-01
# Last reused/audited: 2026-10-02
# Authority basis: standing ENTRY keep-by-value law (operator, 2026-09-30): an open ENTRY
#   rest keeps working toward its current fractional-Kelly target R* (the selector's own
#   mean-q sizer at the rest's limit); a posterior identity change only triggers
#   revaluation; ENTRY rests have no age deadline.
"""Standing ENTRY valuation: R* is the selector's own BUY sizer at the rest's limit,
and the C3 cycle keeps or cancels from it."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
           resolution_at=RESOLUTION_AT, resolver=None):
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
        payoff_q_correction_resolver=resolver,
        resolution_at=resolution_at,
        now=NOW,
    )


class TestTheRestIsValuedAsTheOrderItIs:
    def test_value_is_the_selectors_expected_objective_for_exactly_the_remainder(self):
        value = _value()

        witness = _witness(q=0.75)
        own = _own_view()
        candidate = C._rest_candidate(
            _rest(), snapshot=_snapshot(), binding=witness.bindings[0], side="YES",
            probability_witness=witness, capacity=D("10"),
            ledger_snapshot_id=own.ledger_snapshot_id, now=NOW,
        )
        liquid = own.strategy_capital_allocation.utility_liquid_cash_usd
        du, ev, _eff, cost = S._single_order_metrics(
            candidate, q_samples=np.full(1, 0.75), shares=D("10"),
            wealth_floor_usd=liquid, wealth_ceiling_usd=liquid, alpha=1.0, robust_q=0.75,
        )
        assert value.evidence["conditional_gain"] == pytest.approx(du)
        assert value.evidence["expected_growth"]["expected_ev_usd"] == pytest.approx(ev)
        assert value.evidence["remainder_cost_usd"] == str(cost)
        target = S._global_buy_kelly_reference_target(
            held_shares=D("0"), robust_q=0.75, wealth_floor_usd=liquid,
            wealth_ceiling_usd=liquid, risk_unit_cost=D("0.50"),
        )
        assert D(value.evidence["full_kelly_target_shares"]) == target
        assert D(value.evidence["fractional_kelly_target_shares"]) == target * D("0.125")

    def test_the_selectors_metric_and_growth_laws_are_called_not_reimplemented(self, monkeypatch):
        calls = []
        real_metrics = S._single_order_metrics
        real_growth = S._expected_growth_of_action
        real_positive = S._positive_common_expected_growth

        def spy_metrics(candidate, **kwargs):
            calls.append(("metrics", candidate.execution_mode, kwargs["shares"]))
            return real_metrics(candidate, **kwargs)

        def spy_growth(candidate, **kwargs):
            calls.append(("growth", kwargs["shares"]))
            return real_growth(candidate, **kwargs)

        def spy_positive(growth, **kwargs):
            calls.append(("positive",))
            return real_positive(growth, **kwargs)

        monkeypatch.setattr(S, "_single_order_metrics", spy_metrics)
        monkeypatch.setattr(S, "_expected_growth_of_action", spy_growth)
        monkeypatch.setattr(S, "_positive_common_expected_growth", spy_positive)
        _value(size="10", matched="4")

        assert calls == [("metrics", "MAKER_REST", D("6")), ("growth", D("6")), ("positive",)]

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
        assert own.native_commitments_micro == ((TOKEN, 2_000_000),)
        assert own.spendable_cash_usd == D("103")
        witness = _witness(q=0.75)
        bound = _holdings(witness, own, positions=(position,))
        candidate = C._rest_candidate(
            _rest(matched="4"), snapshot=_snapshot(), binding=witness.bindings[0], side="YES",
            probability_witness=witness, capacity=D("6"), ledger_snapshot_id=own.ledger_snapshot_id,
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
    def test_positive_value_within_target_keeps(self):
        value = _value(q=0.75)
        assert value.action == "KEEP"
        assert value.evidence["authority_valid"] is True
        assert value.evidence["expected_growth"]["capital_lock_hours"] == pytest.approx(36.0)

    def test_order_above_the_selectors_fresh_target_cancels(self):
        value = _value(q=0.75, size="60")
        assert D("60") > D(value.evidence["target_holding_shares"])
        assert value.action == "CANCEL"
        assert value.reason == "CURRENT_FRACTIONAL_TARGET_REDUCED"

    def test_day0_saturated_certainty_is_refuted_like_the_selector(self):
        witness = _witness(q=1.0)
        refuted = _value(
            q=1.0,
            prepared_fields={
                "day0_saturated_statistical_sides": (("bin-rest", "YES"),),
                "day0_saturation_witness_identity": witness.witness_identity,
            },
        )
        assert refuted.action == "CANCEL"
        assert refuted.reason == "ENTRY_REST_BUY_REFUTED:DAY0_STATISTICAL_CERTAINTY_UNSUPPORTED"

    def test_missing_capital_horizon_cancels_protectively(self):
        value = _value(q=0.75, resolution_at=None)
        assert value.action == "CANCEL"
        assert value.evidence["authority_valid"] is False
        assert value.reason == (
            "ENTRY_REST_CAPITAL_HORIZON_INVALID:"
            "EXISTING_BUY_CAPITAL_HORIZON_INVALID:CAPITAL_HORIZON_AUTHORITY_MISSING"
        )

    @pytest.mark.parametrize("q", [0.50, 0.30])
    def test_non_positive_value_at_the_limit_cancels(self, q):
        value = _value(q=q)
        assert value.action == "CANCEL"
        assert value.reason.startswith("CURRENT_MEAN_VALUE_NON_POSITIVE")

    def test_own_reservation_funds_its_rest_without_free_cash(self):
        # $5 free cash plus the rest's own $2.50: the selector would post
        # this one lot fresh, so the existing order is kept.
        assert _value(cash="5", size="5").action == "KEEP"
        # With no wealth beyond its own reservation a fresh lot would exceed
        # full Kelly: the selector posts nothing, so the rest is cancelled.
        assert _value(cash="0", size="5").action == "CANCEL"

    def test_selector_small_capital_rule_keeps_a_one_lot_rest(self):
        # q=0.52: 1/8-Kelly (~1 share) is below one lot but full Kelly (~8)
        # admits one lot, exactly as the selector sizes a fresh order.
        value = _value(q=0.52, size="5")
        assert D(value.evidence["fractional_kelly_target_shares"]) < D("5")
        assert D(value.evidence["full_kelly_target_shares"]) >= D("5")
        assert D(value.evidence["target_holding_shares"]) == D("5")
        assert value.action == "KEEP"

    def test_small_capital_keeps_one_lot_never_up_to_full_kelly(self):
        # Reviewer C3 probe: q 0.55 at 0.50, kT ~2.75 < lot 5 < T ~22. The
        # selector would post exactly one lot, so a 20-share rest sized when
        # q was higher is cancelled; the one-lot rest is kept.
        big = _value(q=0.55, size="20")
        assert D(big.evidence["fractional_kelly_target_shares"]) < D("5")
        assert D(big.evidence["full_kelly_target_shares"]) >= D("20")
        assert D(big.evidence["target_holding_shares"]) == D("5")
        assert (big.action, big.reason) == ("CANCEL", "CURRENT_FRACTIONAL_TARGET_REDUCED")
        assert _value(q=0.55, size="5").action == "KEEP"

    def test_capital_limit_bounds_the_target_like_a_fresh_order(self):
        # A $4 capital limit lets the selector post 8 shares at 0.50.
        value = _value(q=0.75, capital_limit="4")
        assert D(value.evidence["target_holding_shares"]) == D("8")
        assert (value.action, value.reason) == ("CANCEL", "CURRENT_FRACTIONAL_TARGET_REDUCED")
        assert _value(q=0.75, size="8", capital_limit="4").action == "KEEP"

    @pytest.mark.parametrize("q", [0.52, 0.55, 0.6, 0.65, 0.75, 0.9])
    @pytest.mark.parametrize("cash", ["5", "20", "100", "1000"])
    @pytest.mark.parametrize("capital_limit", ["4", "1000"])
    def test_target_is_what_the_selectors_own_sizer_posts(self, q, cash, capital_limit):
        # The fresh order _score_global_single_order_buy_expected sizes at
        # the rest's limit, on the same wealth and capital limit, never ends
        # above the target, and reaches it whenever it places an order.
        value = _value(q=q, cash=cash, capital_limit=capital_limit, size="5")
        witness = _witness(q=q)
        rows = [_obligation_row(shares="5", cost="2.5")]
        own = C._own_reservation_wealth(
            _wealth(cash=cash, reservation="2.5", rows=rows),
            _own(size="5", price="0.50", at_risk_micro=2_500_000),
            obligation_rows=rows, positions=(), native_holdings_micro={},
        )
        deep = C._rest_candidate(
            _rest(size="5"), snapshot=_snapshot(), binding=witness.bindings[0], side="YES",
            probability_witness=witness, capacity=D("100000"),
            ledger_snapshot_id=own.ledger_snapshot_id, now=NOW,
        )
        liquid = own.strategy_capital_allocation.utility_liquid_cash_usd
        fresh = S._score_global_single_order_buy_expected(
            deep, payoff_probability_mean=value.evidence["acting_q"], sample_count=1, band_alpha=1.0,
            wealth_floor_usd=liquid, wealth_ceiling_usd=liquid,
            spendable_cash_usd=own.spendable_cash_usd, capital_limit_usd=D(capital_limit),
            fractional_kelly_multiplier=D("0.125"), current_token_shares=D("0"),
        )
        target = D(value.evidence["target_holding_shares"])
        assert fresh.shares <= target
        assert fresh.shares == 0 and target == 0 or target - fresh.shares < D("0.02")

    def test_posterior_identity_alone_never_changes_the_disposition(self):
        before = _value(q=0.75, posterior="posterior-a")
        after = _value(q=0.75, posterior="posterior-b")

        assert before.action == after.action == "KEEP"
        assert before.evidence["conditional_gain"] == after.evidence["conditional_gain"]
        assert before.evidence["probability_witness_identity"] != (
            after.evidence["probability_witness_identity"]
        )

    def test_rest_age_is_not_an_input(self):
        import inspect

        assert "created_at" not in inspect.getsource(C.value_standing_entry)
        assert "created_at" not in inspect.getsource(C._capture_standing_entry_values)


class TestPartialFillMonotonicity:
    """F3: the disposition depends on the order's full size, never on its fills.

    Reviewer probe matrix: a 5-share rest at 0.50, lot 5, cash 100, k=1/8. At
    q 0.60-0.62 the unfilled rest was KEPT while 1-3 filled shares CANCELLED it,
    stranding unsellable dust (and a higher q flipped KEEP to CANCEL).
    """

    @pytest.mark.parametrize("q", [0.59, 0.60, 0.61, 0.62, 0.70])
    def test_reviewer_matrix_has_one_verdict_per_q(self, q):
        verdicts = {
            matched: _value(q=q, size="5", matched=matched).action
            for matched in ("0", "1", "2", "3", "4")
        }
        assert len(set(verdicts.values())) == 1, verdicts
        assert set(verdicts.values()) == {"KEEP"}

    @pytest.mark.parametrize("size", ["5", "10", "25", "60"])
    @pytest.mark.parametrize("q", [0.52, 0.56, 0.6, 0.75, 0.9])
    def test_verdict_is_monotone_in_filled_size(self, size, q):
        size_d = D(size)
        fills = [str((size_d * D(i) / D(10)).quantize(D("0.01"))) for i in range(10)]
        verdicts = {_value(q=q, size=size, matched=m).action for m in fills}
        assert len(verdicts) == 1, (size, q, verdicts)


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
            world_conn_ro=sqlite3.connect(":memory:"), clock=lambda: NOW,
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
        world_conn_ro=sqlite3.connect(":memory:"), clock=lambda: NOW,
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

    _at, values = C._capture_standing_entry_values(
        conn, sqlite3.connect(":memory:"), sqlite3.connect(":memory:"), entries,
        families={"cmd-bad": FAMILY, "cmd": FAMILY}, clock=lambda: NOW,
    )

    assert [(v.command_id, v.action) for v in values] == [("cmd-bad", "CANCEL"), ("cmd", "CANCEL")]
    assert values[0].reason == "ENTRY_REST_COMMAND_UNREADABLE:OperationalError"
    assert values[1].reason.startswith("ENTRY_REST_CURRENT_SCOPE_UNAVAILABLE")


# ---------------------------------------------------------------------------
# F1 / F2 on the REAL wealth witness and the REAL allocator lifecycle. Only
# the probability authority (scope scan + family prep) is a fixture here; the
# end-to-end real-probability test lives in test_standing_entry_e2e.py.
# ---------------------------------------------------------------------------


def _seed_real_wealth(conn, *, captured_at, pusd_micro=100_000_000):
    """The rest's obligation plus one CHAIN collateral snapshot at ``captured_at``."""
    from src.state.entry_exposure_obligation import open_entry_exposure_obligation
    from src.state.schema.entry_exposure_obligations_schema import ensure_table

    ensure_table(conn)
    open_entry_exposure_obligation(
        conn, command_id="cmd", owner_domain="test", token_id=TOKEN,
        condition_id=f"cond-{TOKEN}", shares=10.0, cost_basis_usd=5.0,
    )
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots ("
        "pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,"
        "captured_at,authority_tier,raw_balance_payload_hash"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pusd_micro, 10**12, 0, "{}", "{}", 0, "{}", captured_at.isoformat(), "CHAIN", "h"),
    )
    conn.commit()


def _real_authority_harness(monkeypatch, *, q=0.75):
    """Probability authority is a fixture; wealth, portfolio and allocator are real."""
    from src.engine import event_reactor_adapter as adapter
    from src.engine import global_auction_universe as universe
    from src.engine import global_batch_runtime as runtime
    from src.engine.qkernel_spine_bridge import PreparedGlobalFamily
    from src.execution import day0_hard_fact_exit

    monkeypatch.setattr(C, "resolve_order_families", lambda *_a: {"cmd": FAMILY})
    monkeypatch.setattr(C, "_snapshot_row", lambda _c, _sid: {**_snapshot(), "condition_id": f"cond-{TOKEN}"})
    event = SimpleNamespace(
        event_id="evt",
        payload_json=json.dumps({"city": FAMILY[0], "target_date": FAMILY[1], "metric": FAMILY[2]}),
    )
    # These tests run on the real clock (the real witness checks snapshot
    # age); the family resolves 36 h after the pass.
    monkeypatch.setattr(
        universe, "scan_current_global_auction_scope",
        lambda **k: SimpleNamespace(
            events=(event,),
            resolution_at_by_family={FAMILY_KEY: k["decision_at_utc"] + timedelta(hours=36)},
        ),
    )
    witness = _witness(q=q)
    bindings = (
        S.OutcomeTokenBinding(bin_id="bin-rest", condition_id=f"cond-{TOKEN}",
                              yes_token_id=TOKEN, no_token_id=f"{TOKEN}-no"),
        witness.bindings[1],
    )
    fields = {
        name: getattr(witness, name)
        for name in (
            "family_key", "q_version", "resolution_identity", "topology_identity",
            "posterior_identity_hash", "source_truth_identity", "authority_certificate_hash",
            "band_alpha", "band_basis", "yes_point_q", "yes_q_samples", "captured_at_utc",
        )
    }
    fields["bindings"] = bindings
    witness = S.JointOutcomeProbabilityWitness(
        **fields, max_age=witness.max_age,
        witness_identity=S.joint_probability_witness_identity(**fields),
    )
    monkeypatch.setattr(
        adapter, "_prepare_current_global_probability_family",
        lambda *_a, **_k: PreparedGlobalFamily(decision_id="d", probability_witness=witness, candidate_seeds=()),
    )
    monkeypatch.setattr(adapter, "_runtime_kelly_multiplier", lambda: 0.125)
    monkeypatch.setattr(
        runtime, "_market_anchored_correction_resolver", lambda *_a, **_k: (lambda *_c: None),
    )
    monkeypatch.setattr(
        "src.runtime.bankroll_provider.current_zeus_capital_allocation_setting",
        lambda: {"mode": "wallet_total"},
    )
    monkeypatch.setattr(day0_hard_fact_exit, "classify_day0_dead_bin_entry_cancels", lambda *_a, **_k: [])


class _NoCancelVenue:
    def __init__(self):
        self.calls = []

    def cancel_orders_batch(self, ids):
        self.calls.append(list(ids))
        return [{"canceled": True, "orderID": i} for i in ids]


def _publish_real_allocator(conn):
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


class TestRealWitnessDecisionInstant:
    """F1: a collateral snapshot written after the job started can never be
    "from the future" for the pass: the decision instant is read after the
    trade read snapshot is pinned."""

    def test_snapshot_landing_after_job_start_keeps_the_rest(self, monkeypatch):
        conn = _trade_db()
        _seed_early_rest(conn)
        _real_authority_harness(monkeypatch)
        _publish_real_allocator(conn)
        job_start = datetime.now(UTC)
        # The snapshot lands 2 s after the job started (it was waiting on the
        # valuation lock), before the pass reads.
        _seed_real_wealth(conn, captured_at=job_start + timedelta(seconds=2))
        ticks = iter([job_start, job_start + timedelta(seconds=3), job_start + timedelta(seconds=3)])

        def clock():
            return next(ticks, job_start + timedelta(seconds=3))

        venue = _NoCancelVenue()
        result = C.run_c3_staleness_cancel_cycle(
            conn, conn, sqlite3.connect(":memory:"), venue,
            world_conn_ro=sqlite3.connect(":memory:"), clock=clock,
        )

        valuation = result["valuations"][0]
        assert valuation.action == "KEEP", valuation.reason
        assert venue.calls == []

    def test_negative_control_a_pre_lock_instant_would_reject_the_same_snapshot(self, monkeypatch):
        # The REAL witness itself refuses a snapshot captured after the
        # decision instant: exactly what a pre-lock `now` handed it.
        from src.engine.global_auction_universe import current_portfolio_wealth_witness
        from src.state.portfolio import load_runtime_open_portfolio

        conn = _trade_db()
        _seed_early_rest(conn)
        monkeypatch.setattr(
            "src.runtime.bankroll_provider.current_zeus_capital_allocation_setting",
            lambda: {"mode": "wallet_total"},
        )
        job_start = datetime.now(UTC)
        _seed_real_wealth(conn, captured_at=job_start + timedelta(seconds=2))
        with pytest.raises(ValueError, match="CURRENT_WEALTH_COLLATERAL_EXPIRED"):
            current_portfolio_wealth_witness(
                conn, decision_at_utc=job_start, max_age=timedelta(minutes=3),
                portfolio_state=load_runtime_open_portfolio(conn),
            )


class TestRealAllocatorLifecycle:
    """F2: before the allocator's first publish the pass decides nothing; a
    later loss of authority still fails closed."""

    def test_unpublished_allocator_defers_every_rest(self, monkeypatch):
        import src.risk_allocator.governor as governor
        from src.risk_allocator import clear_global_allocator

        conn = _trade_db()
        _seed_early_rest(conn)
        _real_authority_harness(monkeypatch)
        at = datetime.now(UTC)
        _seed_real_wealth(conn, captured_at=at - timedelta(seconds=5))
        clear_global_allocator()
        monkeypatch.setattr(governor, "_GLOBAL_ALLOCATOR_EVER_PUBLISHED", False)
        venue = _NoCancelVenue()

        result = C.run_c3_staleness_cancel_cycle(
            conn, conn, sqlite3.connect(":memory:"), venue,
            world_conn_ro=sqlite3.connect(":memory:"), clock=lambda: at,
            authority_pending_until_monotonic=time.monotonic() + 60,
        )

        valuation = result["valuations"][0]
        assert valuation.action == "DEFER"
        assert valuation.reason == "ENTRY_REST_AUTHORITY_PENDING:allocator_not_published"
        assert venue.calls == []
        assert result["deferred"] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM decision_log WHERE mode='standing_entry_revaluation'"
        ).fetchone()[0] == 0

    def test_after_first_publish_the_rest_is_valued(self, monkeypatch):
        import src.risk_allocator.governor as governor

        conn = _trade_db()
        _seed_early_rest(conn)
        _real_authority_harness(monkeypatch)
        monkeypatch.setattr(governor, "_GLOBAL_ALLOCATOR_EVER_PUBLISHED", False)
        _publish_real_allocator(conn)
        at = datetime.now(UTC)
        _seed_real_wealth(conn, captured_at=at - timedelta(seconds=5))

        result = C.run_c3_staleness_cancel_cycle(
            conn, conn, sqlite3.connect(":memory:"), _NoCancelVenue(),
            world_conn_ro=sqlite3.connect(":memory:"), clock=lambda: at,
        )

        assert result["valuations"][0].action == "KEEP", result["valuations"][0].reason

    def test_a_cleared_allocator_after_publish_fails_closed(self, monkeypatch):
        import src.risk_allocator.governor as governor
        from src.risk_allocator import clear_global_allocator

        conn = _trade_db()
        _seed_early_rest(conn)
        _real_authority_harness(monkeypatch)
        monkeypatch.setattr(governor, "_GLOBAL_ALLOCATOR_EVER_PUBLISHED", False)
        _publish_real_allocator(conn)
        clear_global_allocator()
        at = datetime.now(UTC)
        _seed_real_wealth(conn, captured_at=at - timedelta(seconds=5))
        venue = _NoCancelVenue()

        result = C.run_c3_staleness_cancel_cycle(
            conn, conn, sqlite3.connect(":memory:"), venue,
            world_conn_ro=sqlite3.connect(":memory:"), clock=lambda: at,
        )

        valuation = result["valuations"][0]
        assert valuation.action == "CANCEL"
        assert valuation.reason == "ENTRY_REST_PORTFOLIO_AUTHORITY_INVALID:AllocationDenied"
        assert venue.calls == [["venue-1"]]

    def test_uninstalled_fit_corpus_defers_without_an_inline_build(self, monkeypatch):
        import src.calibration.market_anchored_live_fit as fit

        conn = _trade_db()
        _seed_early_rest(conn)
        _real_authority_harness(monkeypatch)
        _publish_real_allocator(conn)
        at = datetime.now(UTC)
        _seed_real_wealth(conn, captured_at=at - timedelta(seconds=5))
        cache = fit.CanonicalCorpusCache()
        loads = []
        cache.builder = SimpleNamespace(served=lambda *_a, **_k: None)
        monkeypatch.setattr(fit, "_SHARED_CANONICAL_CORPUS_CACHE", cache)
        monkeypatch.setattr(fit, "load_canonical_fit_corpus", lambda *a, **k: loads.append(1))
        venue = _NoCancelVenue()

        result = C.run_c3_staleness_cancel_cycle(
            conn, conn, sqlite3.connect(":memory:"), venue,
            world_conn_ro=sqlite3.connect(":memory:"), clock=lambda: at,
            authority_pending_until_monotonic=time.monotonic() + 60,
        )

        valuation = result["valuations"][0]
        assert valuation.action == "DEFER"
        assert valuation.reason == "ENTRY_REST_AUTHORITY_PENDING:fit_corpus_not_installed"
        assert venue.calls == [] and loads == []

    @pytest.mark.parametrize("bound", [None, -1.0])
    def test_not_loaded_authority_past_its_bound_cancels(self, monkeypatch, bound):
        # C2: "not loaded yet" defers only through the bootstrap delay plus
        # one C3 tick; past it (or with no bound) unknown authority fails
        # closed, whatever the rest's economics.
        import src.calibration.market_anchored_live_fit as fit

        conn = _trade_db()
        _seed_early_rest(conn)
        _real_authority_harness(monkeypatch)
        _publish_real_allocator(conn)
        at = datetime.now(UTC)
        _seed_real_wealth(conn, captured_at=at - timedelta(seconds=5))
        cache = fit.CanonicalCorpusCache()
        cache.builder = SimpleNamespace(served=lambda *_a, **_k: None)
        monkeypatch.setattr(fit, "_SHARED_CANONICAL_CORPUS_CACHE", cache)
        venue = _NoCancelVenue()

        result = C.run_c3_staleness_cancel_cycle(
            conn, conn, sqlite3.connect(":memory:"), venue,
            world_conn_ro=sqlite3.connect(":memory:"), clock=lambda: at,
            authority_pending_until_monotonic=None if bound is None else time.monotonic() + bound,
        )

        valuation = result["valuations"][0]
        assert valuation.action == "CANCEL"
        assert valuation.reason == "ENTRY_REST_AUTHORITY_NOT_LOADED_TIMEOUT:fit_corpus_not_installed"
        assert valuation.evidence["authority_valid"] is False
        assert venue.calls == [["venue-1"]]

    def test_unpublished_allocator_past_its_bound_cancels(self, monkeypatch):
        import src.risk_allocator.governor as governor
        from src.risk_allocator import clear_global_allocator

        conn = _trade_db()
        _seed_early_rest(conn)
        _real_authority_harness(monkeypatch)
        at = datetime.now(UTC)
        _seed_real_wealth(conn, captured_at=at - timedelta(seconds=5))
        clear_global_allocator()
        monkeypatch.setattr(governor, "_GLOBAL_ALLOCATOR_EVER_PUBLISHED", False)

        result = C.run_c3_staleness_cancel_cycle(
            conn, conn, sqlite3.connect(":memory:"), _NoCancelVenue(),
            world_conn_ro=sqlite3.connect(":memory:"), clock=lambda: at,
            authority_pending_until_monotonic=time.monotonic() - 1,
        )

        valuation = result["valuations"][0]
        assert (valuation.action, valuation.reason) == (
            "CANCEL", "ENTRY_REST_AUTHORITY_NOT_LOADED_TIMEOUT:allocator_not_published"
        )


class TestFamilyOptimumDominance:
    """C1: the rest is dominated only when cancelling it changes what the
    selector funds by more than the rest is worth: released optimum >
    held optimum + the rest, on the selector's ordering key."""

    def _optimum(self, *, du, ruin=0.0):
        return C.FamilyOptimum(
            candidate_id="fresh", token_id="other", execution_mode="MAKER_REST",
            shares=D("5"), limit_price=D("0.3"), ruin_probability_reduction=ruin,
            expected_delta_log_wealth=du, fill_probability=0.4,
        )

    def test_the_same_optimum_either_way_never_dominates(self):
        # The reviewer's e2e case: the fresh optimum is the same order with
        # or without the rest's cash; cancelling buys nothing.
        keep = _value(q=0.75)
        rest_du = keep.evidence["expected_growth"]["expected_delta_log_wealth"]
        same = self._optimum(du=rest_du * 10)
        assert not C.family_optimum_dominates(keep, held=same, released=same)

    def test_a_worse_sibling_never_dominates_a_partly_filled_rest(self):
        # A small remainder against a full-size worse sibling: the old
        # remainder-vs-fresh comparison cancelled here and stranded dust.
        partial = _value(q=0.75, size="5", matched="4")
        rest_du = partial.evidence["expected_growth"]["expected_delta_log_wealth"]
        sibling = self._optimum(du=rest_du * 3)
        assert not C.family_optimum_dominates(partial, held=sibling, released=sibling)

    def test_dominates_only_when_releasing_the_rest_funds_more_than_it_is_worth(self):
        keep = _value(q=0.75)
        rest_du = keep.evidence["expected_growth"]["expected_delta_log_wealth"]
        held = self._optimum(du=0.01)
        assert C.family_optimum_dominates(
            keep, held=held, released=self._optimum(du=0.01 + rest_du * 1.01)
        )
        assert not C.family_optimum_dominates(
            keep, held=held, released=self._optimum(du=0.01 + rest_du)
        )
        # Cash binds: with the rest held no fresh order fits at all.
        assert C.family_optimum_dominates(keep, held=None, released=self._optimum(du=rest_du * 2))
        assert not C.family_optimum_dominates(keep, held=None, released=self._optimum(du=rest_du / 2))

    def test_no_fresh_optimum_or_a_non_keep_never_dominates(self):
        keep = _value(q=0.75)
        assert not C.family_optimum_dominates(keep, held=None, released=None)
        cancel = _value(q=0.30)
        assert not C.family_optimum_dominates(cancel, held=None, released=self._optimum(du=1.0))

    def test_ruin_reduction_ranks_first(self):
        keep = _value(q=0.75)
        assert C.family_optimum_dominates(
            keep, held=None, released=self._optimum(du=0.0, ruin=0.01)
        )


class TestFreshEntryGateAndPassLocalEvidence:
    """The fresh side passes the live selector's suppression and family-block
    gates, and each pass owns its Day0 ask-repricing evidence."""

    def _cut(self, *, gate, occupied=frozenset()):
        cut = C.FamilyOptimumCut.__new__(C.FamilyOptimumCut)
        cut.trade_conn = _trade_db()
        cut.gate = gate
        cut.occupied_tokens = occupied
        cut.day0_ask_evidence = {}
        cut.event_type = "FORECAST_SNAPSHOT_READY"
        cut.metric = "high"
        cut.truth_by_bin_side = {}
        cut.revision = None
        cut.epoch = object()
        return cut

    def _candidate(self, token="yes-other", family_key=FAMILY_KEY):
        return SimpleNamespace(
            action="BUY", token_id=token, condition_id="cond-other", side="YES",
            bin_id="bin-other", family_key=family_key, book_captured_at_utc=NOW,
        )

    def test_a_global_suppression_refuses_every_fresh_buy(self):
        cut = self._cut(gate=C.FreshEntryGate(global_reason="RISK_ALLOCATOR_GLOBAL_ENTRY_UNAVAILABLE:x",
                                              family_reasons={}))
        assert cut.candidate_policy(self._candidate()) == "RISK_ALLOCATOR_GLOBAL_ENTRY_UNAVAILABLE:x"
        assert cut.optimum(
            portfolio=None, wealth=None, fractional_kelly_multiplier=D("0.125"),
            capital_authority=None, payoff_q_correction_resolver=None,
        ) is None

    def test_a_family_block_refuses_that_familys_fresh_buys_only(self):
        cut = self._cut(gate=C.FreshEntryGate(global_reason=None,
                                              family_reasons={FAMILY_KEY: "EDLI_STAGE_LIVE_CAP_RESERVED"}))
        assert cut.candidate_policy(self._candidate()) == (
            "LIVE_ENTRY_BLOCKED:entry_readiness_family:EDLI_STAGE_LIVE_CAP_RESERVED"
        )

    def test_the_rests_own_token_is_never_a_fresh_alternative(self):
        cut = self._cut(gate=C.FRESH_ENTRY_GATE_OPEN, occupied=frozenset({TOKEN}))
        assert cut.candidate_policy(self._candidate(token=TOKEN)) == "STANDING_ENTRY_TOKEN_HAS_OPEN_REST"

    def test_day0_ask_evidence_is_owned_by_the_pass(self, monkeypatch):
        from src.engine import event_reactor_adapter as adapter

        seen = []

        def record(candidate, *, event_type, trade_conn, counts):
            seen.append(counts)
            counts[("tok", NOW)] = 1
            return "STOP"

        monkeypatch.setattr(adapter, "_day0_candidate_ask_repricing_rejection_reason", record)
        monkeypatch.setattr(adapter, "_global_active_entry_duplicate_reason", lambda *_a, **_k: None)
        shared_before = dict(adapter._DAY0_ASK_SELECTION_EVIDENCE)
        first, second = self._cut(gate=C.FRESH_ENTRY_GATE_OPEN), self._cut(gate=C.FRESH_ENTRY_GATE_OPEN)
        first.candidate_policy(self._candidate())
        second.candidate_policy(self._candidate())

        assert seen[0] is first.day0_ask_evidence and seen[1] is second.day0_ask_evidence
        assert seen[0] is not seen[1]
        assert all(m is not adapter._DAY0_ASK_SELECTION_EVIDENCE for m in seen)
        assert adapter._DAY0_ASK_SELECTION_EVIDENCE == shared_before


# ---------------------------------------------------------------------------
# D1: the C3 keep target is invariant in how a fixed order is split between
# filled h and remaining r, also when the allocator's headroom binds. The
# allocator's capacity is HEADROOM (cap - weighted exposure of its own lots,
# risk_allocator.governor auction_capacity / _remaining_capacity), and a
# partly filled order's own fill is in those lots as soon as its position is
# current (load_position_lots), so the pre-fill state adds that fill back to
# the allocator's headroom, the cash and the loss-branch wealth alike.
# ---------------------------------------------------------------------------


def _split_capture(monkeypatch, *, size, filled, cap_usd, price, q, cash="400", lot_state="CONFIRMED_EXPOSURE"):
    """One _capture_standing_entry_values pass on the production capital path:
    real wealth witness over a current position carrying the order's own
    fill (as live projects it at the first partial fill), a real
    RiskAllocator whose lots carry that fill, and a per-market cap."""
    from src.control.heartbeat_supervisor import HeartbeatHealth
    from src.risk_allocator import (
        CapPolicy,
        ExposureLot,
        GovernorState,
        RiskAllocator,
        configure_global_allocator,
    )
    from src.state import portfolio as portfolio_module
    from src.state.entry_exposure_obligation import open_entry_exposure_obligation
    from src.state.schema.entry_exposure_obligations_schema import ensure_table
    from tests.execution.test_staleness_cancel import _seed_open_entry

    _real_authority_harness(monkeypatch, q=q)
    conn = _trade_db()
    at = datetime.now(UTC)
    p, n, h = D(price), D(size), D(filled)
    _seed_open_entry(
        conn, command_id="cmd", token_id=TOKEN, venue_order_id="venue-1", q_version="q-submitted",
        created_at=at - timedelta(hours=3), fact_state="PARTIALLY_MATCHED" if h else "LIVE",
        matched_size=str(h), remaining_size=str(n - h),
    )
    conn.execute(
        "UPDATE venue_commands SET size=?, price=?, position_id='pos-cmd' WHERE command_id='cmd'",
        (float(n), float(p)),
    )
    conn.execute(
        "INSERT INTO collateral_reservations (command_id, reservation_type, amount, created_at) "
        "VALUES ('cmd', 'PUSD_BUY', ?, ?)",
        (int(n * p * 1_000_000), at.isoformat()),
    )
    ensure_table(conn)
    open_entry_exposure_obligation(
        conn, command_id="cmd", owner_domain="test", token_id=TOKEN, condition_id=f"cond-{TOKEN}",
        shares=float(n), cost_basis_usd=float(n * p),
    )
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots (pusd_balance_micro,pusd_allowance_micro,"
        "usdc_e_legacy_balance_micro,ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,captured_at,authority_tier,"
        "raw_balance_payload_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            int((D(cash) - h * p) * 1_000_000), 10**12, 0,
            json.dumps({TOKEN: int(h * 1_000_000)} if h else {}), "{}",
            int(n * p * 1_000_000), "{}", (at - timedelta(seconds=5)).isoformat(), "CHAIN", "h",
        ),
    )
    conn.commit()
    if h:
        position = SimpleNamespace(
            position_id="pos-cmd", trade_id="pos-cmd", direction="buy_yes", token_id=TOKEN,
            no_token_id=f"{TOKEN}-no", condition_id=f"cond-{TOKEN}", shares=float(h),
            cost_basis_usd=float(h * p), entry_price=float(p), chain_state="synced",
            chain_shares=float(h), state="active", city=FAMILY[0], target_date=FAMILY[1],
            temperature_metric=FAMILY[2], entry_method="", strategy_key="",
        )
        real_load = portfolio_module.load_runtime_open_portfolio

        def with_own_fill(c):
            from dataclasses import replace as dc_replace

            return dc_replace(real_load(c), positions=[position])

        monkeypatch.setattr(portfolio_module, "load_runtime_open_portfolio", with_own_fill)
    # lot_state None: the fill is not in the allocator's lots yet (it was
    # published before the fill), so its headroom does not move with fills.
    lots = (
        [ExposureLot(market_id="gamma", event_id="event", resolution_window="default", token_id=TOKEN,
                     exposure_micro=int(h * p * 1_000_000), state=lot_state, correlation_key=FAMILY_KEY)]
        if h and lot_state else []
    )
    configure_global_allocator(
        RiskAllocator(CapPolicy(max_per_market_micro=int(D(cap_usd) * 1_000_000)), lots),
        GovernorState(
            current_drawdown_pct=0.0, heartbeat_health=HeartbeatHealth.HEALTHY, ws_gap_active=False,
            ws_gap_seconds=0, unknown_side_effect_count=0, reconcile_finding_count=0,
        ),
    )
    monkeypatch.setattr(
        C, "_snapshot_row", lambda _c, _sid: {**_snapshot(), "condition_id": f"cond-{TOKEN}"},
    )
    _at, values = C._capture_standing_entry_values(
        conn, sqlite3.connect(":memory:"), sqlite3.connect(":memory:"),
        C.find_open_entry_rests(conn), families={"cmd": FAMILY}, clock=lambda: at,
    )
    return values[0]


class TestFillSplitInvarianceUnderABindingCap:
    SPLITS = ("0", "1", "3", "3.9", "5.2", "12.99")

    @pytest.mark.parametrize("lot_state", [None, "CONFIRMED_EXPOSURE", "OPTIMISTIC_EXPOSURE"])
    def test_reviewer_four_dollar_cap_on_a_thirteen_share_rest(self, monkeypatch, lot_state):
        # Reviewer D1: a $4 per-market cap, 13 sh @ 0.50. At 2f3451ac3 the
        # target rose 1:1 with fills: CANCEL at 0-3.9 filled, KEEP at >= 5.2.
        verdicts = {}
        for h in self.SPLITS:
            v = _split_capture(
                monkeypatch, size="13", filled=h, cap_usd="4", price="0.50", q=0.70, lot_state=lot_state,
            )
            verdicts[h] = (v.action, v.reason, D(v.evidence["target_holding_shares"]))
        assert {a for a, _r, _t in verdicts.values()} == {"CANCEL"}, verdicts
        assert {t for _a, _r, t in verdicts.values()} == {D("8")}, verdicts
        assert {r for _a, r, _t in verdicts.values()} == {"CURRENT_FRACTIONAL_TARGET_REDUCED"}

    @pytest.mark.parametrize("price,size", [("0.50", "13"), ("0.30", "20"), ("0.65", "9")])
    @pytest.mark.parametrize("cap", ["3", "4", "6", "1000"])
    @pytest.mark.parametrize("lot_state", [None, "CONFIRMED_EXPOSURE", "OPTIMISTIC_EXPOSURE"])
    def test_decision_is_invariant_in_the_fill_split(self, monkeypatch, price, size, cap, lot_state):
        splits = [h for h in self.SPLITS if D(h) < D(size)]
        decisions = {
            h: _split_capture(
                monkeypatch, size=size, filled=h, cap_usd=cap, price=price, q=0.75, lot_state=lot_state,
            )
            for h in splits
        }
        verdicts = {(v.action, v.reason.split(":")[0]) for v in decisions.values()}
        assert len(verdicts) == 1, {h: (v.action, v.reason, v.evidence.get("target_holding_shares"))
                                    for h, v in decisions.items()}


class TestSharedTokenRestorationNeverOverRestores:
    """E1: the allocator's lots carry no position identity, so another current
    position on the rest's token is indistinguishable from the rest's own
    fill. Restored headroom must never exceed the true pre-fill value (the
    lots with only the own fill removed): any error leans toward CANCEL."""

    CAP = D("10")

    def _authority(self, lots):
        from src.risk_allocator import AuctionCapitalAuthority, CapPolicy, ExposureLot, RiskAllocator

        return AuctionCapitalAuthority(
            RiskAllocator(
                CapPolicy(max_per_market_micro=int(self.CAP * 1_000_000)),
                [
                    ExposureLot(market_id="gamma", event_id="event", resolution_window="default",
                                token_id=TOKEN, exposure_micro=int(D(usd) * 1_000_000), state=state,
                                correlation_key=FAMILY_KEY)
                    for usd, state in lots
                ],
            )
        )

    def _headroom(self, lots, *, own_fill, other):
        return C._prefill_allocator_capacity_usd(
            self._authority(lots), market_id="gamma", event_id="event", correlation_key=FAMILY_KEY,
            token_id=TOKEN, own_filled_cost_usd=D(own_fill), other_token_exposure_usd=D(other),
        )

    def _truth(self, other_lots):
        return Decimal(self._authority(other_lots).capacity_usd(
            market_id="gamma", event_id="event", correlation_key=FAMILY_KEY,
        ))

    @pytest.mark.parametrize(
        "own_lots,other_lots,own_fill,other",
        [
            # Reviewer probe 1: own fill unpublished, other holding $3 CONFIRMED.
            ([], [("3", "CONFIRMED_EXPOSURE")], "2.6", "3"),
            # Reviewer probe 2: own fill partly published OPTIMISTIC, other $3 CONFIRMED.
            ([("1.3", "OPTIMISTIC_EXPOSURE")], [("3", "CONFIRMED_EXPOSURE")], "2.6", "3"),
            # Reviewer probe 3: own fill CONFIRMED, other $2.60 OPTIMISTIC.
            ([("2.6", "CONFIRMED_EXPOSURE")], [("2.6", "OPTIMISTIC_EXPOSURE")], "2.6", "2.6"),
        ],
        ids=["own_unpublished", "own_partly_optimistic", "other_optimistic"],
    )
    def test_reviewer_probes_never_exceed_the_true_prefill_headroom(
        self, own_lots, other_lots, own_fill, other,
    ):
        headroom = self._headroom([*own_lots, *other_lots], own_fill=own_fill, other=other)
        current = self._truth([*own_lots, *other_lots])
        truth = self._truth(other_lots)
        assert current <= headroom <= truth, (current, headroom, truth)

    def test_without_another_holding_the_own_fill_is_restored_exactly(self):
        for state in ("CONFIRMED_EXPOSURE", "OPTIMISTIC_EXPOSURE"):
            assert self._headroom([("2.6", state)], own_fill="2.6", other="0") == self.CAP

    def test_other_holdings_exposure_is_valued_as_the_allocator_values_a_position(self):
        other = SimpleNamespace(position_id="pos-other", trade_id="pos-other", direction="buy_yes",
                                token_id=TOKEN, no_token_id="no", shares=4.0, chain_shares=5.0,
                                cost_basis_usd=1.0, chain_cost_basis_usd=1.5, entry_price=0.6)
        own = SimpleNamespace(position_id="pos-cmd", trade_id="pos-cmd", direction="buy_yes",
                              token_id=TOKEN, no_token_id="no", shares=9.0, chain_shares=9.0,
                              cost_basis_usd=4.5, chain_cost_basis_usd=4.5, entry_price=0.5)
        unrelated = SimpleNamespace(position_id="pos-x", trade_id="pos-x", direction="buy_no",
                                    token_id=TOKEN, no_token_id="other-no", shares=9.0, chain_shares=0.0,
                                    cost_basis_usd=9.0, chain_cost_basis_usd=0.0, entry_price=1.0)
        assert C._other_token_exposure_usd(
            [other, own, unrelated], token_id=TOKEN, own_position_id="pos-cmd",
        ) == D("3.0")
