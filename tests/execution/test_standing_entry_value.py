# Created: 2026-10-01
# Last reused/audited: 2026-10-04
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
from dataclasses import replace
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
    def _cycle(self, monkeypatch, *, q, posterior, identity=None):
        import src.risk_allocator as risk_allocator
        from src.engine import event_reactor_adapter as adapter
        from src.engine import global_auction_universe as universe
        from src.engine.qkernel_spine_bridge import PreparedGlobalFamily
        from src.execution import day0_hard_fact_exit
        from src.state import portfolio as portfolio_module
        from src.state.snapshot_repo import get_snapshot, insert_snapshot

        conn = _trade_db()
        _seed_early_rest(conn)
        submission = get_snapshot(conn, "snap-cmd")
        monkeypatch.setattr(C, "resolve_order_families", lambda *_a: {"cmd": FAMILY})
        monkeypatch.setattr(
            C, "_snapshot_row", lambda _c, _sid: {
                **_snapshot(), "condition_id": submission.condition_id,
                "no_token_id": submission.no_token_id,
            },
        )
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
        witness = S.rebind_family_payoff_witness(witness, bindings=(
            replace(
                witness.bindings[0], condition_id=submission.condition_id,
                no_token_id=submission.no_token_id,
            ),
            witness.bindings[1],
        ))
        if identity is not None:
            for binding in witness.bindings:
                for side, token in (("YES", binding.yes_token_id), ("NO", binding.no_token_id)):
                    insert_snapshot(conn, replace(
                        submission, snapshot_id=f"stale-{binding.condition_id}-{side}",
                        condition_id=binding.condition_id,
                        yes_token_id=binding.yes_token_id, no_token_id=binding.no_token_id,
                        selected_outcome_token_id=token, outcome_label=side,
                        token_map_raw={"YES": binding.yes_token_id, "NO": binding.no_token_id},
                        captured_at=NOW - timedelta(minutes=5),
                        freshness_deadline=NOW - timedelta(minutes=4),
                    ))
            sibling = witness.bindings[1]
            if identity == "ambiguous":
                insert_snapshot(conn, replace(
                    submission, snapshot_id="conflicting-sibling",
                    condition_id=sibling.condition_id,
                    yes_token_id=sibling.yes_token_id, no_token_id="conflicting-no",
                    selected_outcome_token_id=sibling.yes_token_id, outcome_label="YES",
                    token_map_raw={"YES": sibling.yes_token_id, "NO": "conflicting-no"},
                    captured_at=NOW - timedelta(minutes=4),
                    freshness_deadline=NOW - timedelta(minutes=3),
                ))
            conn.commit()
            witness = S.rebind_family_payoff_witness(witness, bindings=(
                witness.bindings[0], replace(sibling, no_token_id=None),
            ))
        forecast = sqlite3.connect(":memory:")
        forecast.execute(
            "CREATE TABLE market_events (condition_id TEXT, market_slug TEXT, created_at TEXT)"
        )
        forecast.executemany(
            "INSERT INTO market_events VALUES (?,?,?)",
            [(b.condition_id, "current-family-slug", NOW.isoformat()) for b in witness.bindings],
        )
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
            risk_allocator, "snapshot_global_auction_capital_authority",
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
            conn, conn, forecast, venue,
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

    @pytest.mark.parametrize("identity", ("stale", "ambiguous"))
    def test_partial_witness_uses_persisted_stale_sibling_identity(self, monkeypatch, identity):
        conn, venue, result = self._cycle(
            monkeypatch, q=0.75, posterior="posterior-NEW", identity=identity,
        )
        valuation, = result["valuations"]
        artifact = json.loads(conn.execute(
            "SELECT artifact_json FROM decision_log WHERE mode='standing_entry_revaluation'"
        ).fetchone()[0])
        persisted = conn.execute(
            "SELECT yes_token_id, no_token_id, freshness_deadline "
            "FROM executable_market_snapshot_latest WHERE condition_id='cond-other'"
        ).fetchall()
        assert len(persisted) == 2
        assert all(datetime.fromisoformat(row["freshness_deadline"]) < NOW for row in persisted)
        state = conn.execute(
            "SELECT state FROM venue_commands WHERE command_id='cmd'"
        ).fetchone()[0]
        if identity == "stale":
            assert {(row["yes_token_id"], row["no_token_id"]) for row in persisted} == {
                ("yes-other", "no-other"),
            }
            assert valuation.action == artifact["action"] == "KEEP", valuation.reason
            assert valuation.reason == "CURRENT_ENTRY_REST_VALUE_POSITIVE"
            assert valuation.evidence["authority_valid"] is True
            assert artifact["evidence"]["authority_valid"] is True
            assert artifact["evidence"]["posterior_identity_hash"] == "posterior-NEW"
            assert result["cancel_set_size"] == 0
            assert state == "ACKED"
            assert venue.calls == []
        else:
            assert valuation.action == artifact["action"] == "CANCEL"
            assert valuation.reason == (
                "ENTRY_REST_TOKEN_IDENTITY_UNAVAILABLE:ValueError:"
                "GLOBAL_LOCAL_TOKEN_IDENTITY_AMBIGUOUS:cond-other"
            )
            assert valuation.evidence["authority_valid"] is False
            assert artifact["evidence"]["authority_valid"] is False
            assert result["cancel_set_size"] == 1
            assert state == "CANCELLED"
            assert venue.calls == [(["venue-1"], "CANCEL_PENDING")]
            payload = json.loads(conn.execute(
                "SELECT payload_json FROM venue_command_events "
                "WHERE command_id='cmd' AND event_type='CANCEL_REQUESTED'"
            ).fetchone()[0])
            assert payload["cancel_reason"] == valuation.reason

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


class TestUnprovableBuyCash:
    """A witness whose reservations exceed chain pUSD vetoes every BUY. A rest
    is a BUY still filling, so it cancels too, whatever else the floor holds."""

    @pytest.mark.parametrize("legacy_micro", [0, 1_000_000_000])
    def test_a_typed_buy_cash_veto_cancels_the_rest(self, monkeypatch, legacy_micro):
        conn = _trade_db()
        _seed_early_rest(conn)
        _real_authority_harness(monkeypatch)
        _publish_real_allocator(conn)
        at = datetime.now(UTC)
        # $3 pUSD under the rest's own $5 reservation.
        _seed_real_wealth(conn, captured_at=at - timedelta(seconds=5), pusd_micro=3_000_000)
        conn.execute(
            "UPDATE collateral_ledger_snapshots SET usdc_e_legacy_balance_micro=?", (legacy_micro,)
        )
        conn.commit()
        venue = _NoCancelVenue()

        result = C.run_c3_staleness_cancel_cycle(
            conn, conn, sqlite3.connect(":memory:"), venue,
            world_conn_ro=sqlite3.connect(":memory:"), clock=lambda: at,
        )

        valuation = result["valuations"][0]
        assert (valuation.action, valuation.reason) == (
            "CANCEL",
            "ENTRY_REST_PORTFOLIO_AUTHORITY_INVALID:CURRENT_WEALTH_SPENDABLE_CASH_INVALID",
        )
        assert valuation.evidence["authority_valid"] is False
        assert venue.calls == [["venue-1"]]


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


def _abc_witness(q=(0.20, 0.42, 0.38)) -> S.JointOutcomeProbabilityWitness:
    """Three mutually exclusive outcomes A/B/C; the rest's token is YES-A."""
    bindings = tuple(
        S.OutcomeTokenBinding(
            bin_id=bin_id, condition_id=CONDITION if bin_id == "A" else f"cond-{bin_id}",
            yes_token_id=TOKEN if bin_id == "A" else f"yes-{bin_id}", no_token_id=f"no-{bin_id}",
        )
        for bin_id in "ABC"
    )
    samples = np.tile(np.asarray(q, dtype=np.float64), (400, 1))
    fields = dict(
        family_key=FAMILY_KEY, bindings=bindings, q_version="q-abc", resolution_identity="resolution",
        topology_identity="topology", posterior_identity_hash="posterior-abc", source_truth_identity="source",
        authority_certificate_hash="certificate-abc", band_alpha=0.05, band_basis="joint_q_band_samples",
        yes_point_q=np.mean(samples, axis=0), yes_q_samples=samples, captured_at_utc=NOW,
    )
    return S.JointOutcomeProbabilityWitness(
        **fields, max_age=timedelta(minutes=3), witness_identity=S.joint_probability_witness_identity(**fields),
    )


class TestFamilyOptimumDominance:
    """C1: the rest is dominated only when the family's plan with it cancelled
    (the released optimum) beats the plan with it kept (its remainder plus the
    held optimum), both valued whole on one wealth and one outcome vector."""

    def _abc(self, *, cash="49.4", size="10", price="0.06"):
        """A real KEEP of ``size`` YES-A @ ``price`` on $``cash`` + its own
        reservation, and the family endowment of the world without it."""
        from src.engine.global_single_order_auction import _family_portfolio_endowment

        witness = _abc_witness()
        cost = str(D(size) * D(price))
        rows = [_obligation_row(shares=size, cost=cost)]
        own = C._own_reservation_wealth(
            _wealth(cash=cash, reservation=cost, rows=rows),
            _own(size=size, price=price, at_risk_micro=int(D(cost) * 1_000_000)),
            obligation_rows=rows, positions=(), native_holdings_micro={},
        )
        holdings = _holdings(witness, own)
        keep = C.value_standing_entry(
            _rest(size=size, price=price), family=FAMILY, snapshot=_snapshot(),
            prepared=_prepared(witness), wealth=own, holdings_snapshot=holdings,
            fractional_kelly_multiplier=D("0.125"), capital_limit_usd=D("100"),
            payoff_q_correction_resolver=None, resolution_at=RESOLUTION_AT, now=NOW,
        )
        assert keep.action == "KEEP", keep.reason
        endowment = _family_portfolio_endowment(
            probability_witness=witness, holdings_snapshot=holdings, wealth_witness=own,
        )
        return keep, witness, endowment

    def _optimum(self, witness, bin_id, shares, price, *, side="YES", du=0.0, fills=None, q=None):
        shares, price = D(shares), D(price)
        binding = next(b for b in witness.bindings if b.bin_id == bin_id)
        return C.FamilyOptimum(
            candidate_id=f"fresh-{bin_id}",
            token_id=binding.yes_token_id if side == "YES" else binding.no_token_id,
            execution_mode="TAKER_LIMIT",
            shares=shares, limit_price=price, ruin_probability_reduction=0.0,
            expected_delta_log_wealth=du, fill_probability=1.0, bin_id=bin_id, side=side,
            acting_q=S.family_payoff_point_q(witness, bin_id=bin_id, side=side) if q is None else q,
            fills=fills or ((D("1"), shares, shares * price),),
        )

    def _dominates(self, keep, witness, endowment, *, held, released):
        return C.family_optimum_dominates(
            keep, held=held, released=released, probability_witness=witness, endowment=endowment,
        )

    def test_keep_plan_is_valued_on_the_family_outcomes_not_a_sum_of_order_growths(self):
        # The reviewer's counterexample: $50, 10 YES-A @ 0.06 rest, held 27.14
        # YES-B @ 0.07, released 33.60 YES-B @ 0.07, certain fills. Each
        # order's growth on its own binary projection sums below the released
        # one (the additive comparator cancelled), but on the actual A/B/C
        # wealth states {10 A, 27.14 B} beats {33.60 B}.
        import math

        keep, witness, endowment = self._abc()
        assert endowment.wealth_floor_usd == D("50")

        def binary_du(q, shares, cost, floor):
            return q * math.log((floor - cost + shares) / floor) + (1 - q) * math.log((floor - cost) / floor)

        held_du = binary_du(0.42, 27.14, 27.14 * 0.07, 49.4)  # the rest's $0.60 held
        released_du = binary_du(0.42, 33.60, 33.60 * 0.07, 50.0)
        rest_du = keep.evidence["expected_growth"]["expected_delta_log_wealth"]
        assert rest_du + held_du < released_du  # the additive key's CANCEL
        held = self._optimum(witness, "B", "27.14", "0.07", du=held_du)
        released = self._optimum(witness, "B", "33.60", "0.07", du=released_du)
        kept = C._plan_growth(("A", "YES", D("10"), D("0.6")), held, witness=witness, endowment=endowment)
        cancelled = C._plan_growth(None, released, witness=witness, endowment=endowment)
        assert kept == pytest.approx(0.1767388, abs=1e-7)
        assert cancelled == pytest.approx(0.1759572, abs=1e-7)
        assert not self._dominates(keep, witness, endowment, held=held, released=released)

    def test_dominates_only_when_the_cancel_plan_is_strictly_better(self, monkeypatch):
        keep, witness, endowment = self._abc()
        better = self._optimum(witness, "B", "33.60", "0.07")
        # Cash binds: with the rest held no fresh order fits at all.
        assert self._dominates(keep, witness, endowment, held=None, released=better)
        assert not self._dominates(
            keep, witness, endowment, held=None, released=self._optimum(witness, "B", "1", "0.07"),
        )
        # Equal plans: a tie keeps.
        monkeypatch.setattr(C, "_plan_growth", lambda *_a, **_k: 0.1)
        assert not self._dominates(keep, witness, endowment, held=None, released=better)

    def test_a_released_optimum_on_the_rests_own_token_never_dominates(self):
        # Released, the rest's token is free; the selector buying it again,
        # even more of it or cheaper, is the rest re-priced or re-sized.
        keep, witness, endowment = self._abc()
        again = self._optimum(witness, "A", "40", "0.05")
        assert again.token_id == keep.token_id
        assert C._plan_growth(None, again, witness=witness, endowment=endowment) > C._plan_growth(
            ("A", "YES", D("10"), D("0.6")), None, witness=witness, endowment=endowment,
        )
        assert not self._dominates(keep, witness, endowment, held=None, released=again)
        # The same order on the rest's NO token is another claim, not the rest.
        other_side = self._optimum(witness, "B", "40", "0.05", side="NO")
        assert self._dominates(keep, witness, endowment, held=None, released=other_side)

    def test_a_fresh_maker_is_weighted_by_its_fill_witness(self):
        keep, witness, endowment = self._abc()
        shares, price = D("33.60"), D("0.07")
        certain = self._optimum(witness, "B", shares, price)
        assert self._dominates(keep, witness, endowment, held=None, released=certain)
        # Filled one time in ten, the replacement is worth less than the rest.
        rarely = self._optimum(witness, "B", shares, price, fills=(
            (D("0.9"), D("0"), D("0")), (D("0.1"), shares, shares * price),
        ))
        assert not self._dominates(keep, witness, endowment, held=None, released=rarely)

    def test_no_shared_outcome_vector_never_dominates(self):
        keep, witness, endowment = self._abc()
        better = self._optimum(witness, "B", "33.60", "0.07")
        assert not self._dominates(keep, witness, endowment, held=None, released=None)
        cancel = replace(keep, action="CANCEL")
        assert not self._dominates(cancel, witness, endowment, held=None, released=better)
        # An order scored on a calibrated q is not on the vector's own law.
        calibrated = self._optimum(witness, "B", "33.60", "0.07", q=0.40)
        assert not self._dominates(keep, witness, endowment, held=None, released=calibrated)
        recalibrated = replace(keep, evidence={**keep.evidence, "acting_q": 0.25})
        assert not self._dominates(recalibrated, witness, endowment, held=None, released=better)
        exact = SimpleNamespace(family_key=FAMILY_KEY, bin_ids=witness.bin_ids)
        assert not self._dominates(keep, exact, endowment, held=None, released=better)


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

    def test_the_released_world_frees_only_its_own_rests_token(self, monkeypatch):
        # The rest never placed: its token meets neither the open-rest
        # exclusion nor the active-order lock. Every other rest's token stays
        # occupied and every other token still meets the lock.
        from src.engine import event_reactor_adapter as adapter

        monkeypatch.setattr(adapter, "_global_active_entry_duplicate_reason", lambda *_a, **_k: "ACTIVE_ORDER")
        monkeypatch.setattr(
            adapter, "_day0_candidate_ask_repricing_rejection_reason", lambda *_a, **_k: "PAST_REST_EXCLUSIONS",
        )
        cut = self._cut(gate=C.FRESH_ENTRY_GATE_OPEN, occupied=frozenset({TOKEN, "yes-sibling"}))
        own, sibling, free = (self._candidate(token=t) for t in (TOKEN, "yes-sibling", "yes-free"))
        assert cut.candidate_policy(own) == "STANDING_ENTRY_TOKEN_HAS_OPEN_REST"
        assert cut.candidate_policy(own, vacated=TOKEN) == "PAST_REST_EXCLUSIONS"
        assert cut.candidate_policy(sibling, vacated=TOKEN) == "STANDING_ENTRY_TOKEN_HAS_OPEN_REST"
        assert cut.candidate_policy(free, vacated=TOKEN) == "ACTIVE_ORDER"

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


# ---------------------------------------------------------------------------
# E1/F1: restored allocator headroom is never above the true pre-fill headroom.
# Driven through _capture_standing_entry_values (an interface every commit of
# this stack has) with a real RiskAllocator and a real portfolio row: the
# capital limit the pass hands value_standing_entry is spied, and its
# allocator part is checked against truth = the allocator's own capacity on
# the lots with ONLY this order's fill removed. A cap well below the strategy
# and single-position limits makes the allocator term the binding one.
# ---------------------------------------------------------------------------

_CAP = D("6")


def _matrix_capture(monkeypatch, *, older_own, this_fill, row_has_fill, lot_state, other):
    """Run one C3 capture and return (spied capital limit, truth headroom).

    older_own: an earlier ENTRY order on the SAME position already filled
    (its lot is in the allocator and its shares are in the position row).
    this_fill: this rest's own filled shares at 0.50; row_has_fill: the
    position row already carries them; lot_state: how this fill is in the
    allocator's lots (None = not yet published). other: another position on
    the same token as (cost, chain cost, shares, entry price, lot state)."""
    from src.control.heartbeat_supervisor import HeartbeatHealth
    from src.risk_allocator import (
        AuctionCapitalAuthority,
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

    _real_authority_harness(monkeypatch, q=0.75)
    conn = _trade_db()
    at = datetime.now(UTC)
    price, size, h = D("0.50"), D("13"), D(this_fill)
    _seed_open_entry(
        conn, command_id="cmd", token_id=TOKEN, venue_order_id="venue-1", q_version="q-submitted",
        created_at=at - timedelta(hours=1), fact_state="PARTIALLY_MATCHED" if h else "LIVE",
        matched_size=str(h), remaining_size=str(size - h),
    )
    conn.execute("UPDATE venue_commands SET size=13.0, position_id='pos-cmd' WHERE command_id='cmd'")
    older_shares = D("6") if older_own else D("0")
    if older_own:
        # The earlier ENTRY on the same position: filled 6 sh @ 0.50, terminal.
        _seed_open_entry(
            conn, command_id="cmd-older", token_id=TOKEN, venue_order_id="venue-0",
            q_version="q-older", created_at=at - timedelta(hours=5),
            fact_state="MATCHED", matched_size="6", remaining_size="0",
        )
        conn.execute(
            "UPDATE venue_commands SET position_id='pos-cmd', state='FILLED', size=6.0 "
            "WHERE command_id='cmd-older'"
        )
    conn.execute(
        "INSERT INTO collateral_reservations (command_id, reservation_type, amount, created_at) "
        "VALUES ('cmd', 'PUSD_BUY', ?, ?)", (int(size * price * 1_000_000), at.isoformat()),
    )
    ensure_table(conn)
    open_entry_exposure_obligation(
        conn, command_id="cmd", owner_domain="test", token_id=TOKEN, condition_id=f"cond-{TOKEN}",
        shares=float(size), cost_basis_usd=float(size * price),
    )
    row_shares = older_shares + (h if row_has_fill else D("0"))
    # The wealth witness attributes a token's chain balance whole to each
    # position on it, so with two positions on one token the snapshot leaves
    # the token out and each position carries its own shares.
    chain_shares = row_shares if not other else D("0")
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots (pusd_balance_micro,pusd_allowance_micro,"
        "usdc_e_legacy_balance_micro,ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,captured_at,authority_tier,"
        "raw_balance_payload_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            int((D("400") - (older_shares + h) * price) * 1_000_000), 10**12, 0,
            json.dumps({TOKEN: int(chain_shares * 1_000_000)} if chain_shares else {}),
            "{}", int(size * price * 1_000_000), "{}", (at - timedelta(seconds=5)).isoformat(), "CHAIN", "h",
        ),
    )
    conn.commit()

    def position(pid, *, shares, cost, chain_cost, entry):
        return SimpleNamespace(
            position_id=pid, trade_id=pid, direction="buy_yes", token_id=TOKEN, no_token_id=f"{TOKEN}-no",
            condition_id=f"cond-{TOKEN}", shares=float(shares), cost_basis_usd=float(cost),
            chain_cost_basis_usd=float(chain_cost), entry_price=float(entry), chain_state="synced",
            chain_shares=float(shares), state="active", city=FAMILY[0], target_date=FAMILY[1],
            temperature_metric=FAMILY[2], entry_method="", strategy_key="",
        )

    positions = []
    if row_shares:
        positions.append(position("pos-cmd", shares=row_shares, cost=row_shares * price,
                                  chain_cost=row_shares * price, entry=price))
    if other:
        cost, chain_cost, shares, entry, _state = other
        positions.append(position("pos-other", shares=D(shares), cost=D(cost), chain_cost=D(chain_cost),
                                  entry=D(entry)))
    if positions:
        real_load = portfolio_module.load_runtime_open_portfolio

        def with_positions(c):
            from dataclasses import replace as dc_replace

            return dc_replace(real_load(c), positions=list(positions))

        monkeypatch.setattr(portfolio_module, "load_runtime_open_portfolio", with_positions)

    def lot(usd, state):
        return ExposureLot(market_id="gamma", event_id="event", resolution_window="default", token_id=TOKEN,
                           exposure_micro=int(D(usd) * 1_000_000), state=state, correlation_key=FAMILY_KEY)

    other_lots = []
    if older_own:
        other_lots.append(lot(older_shares * price, "CONFIRMED_EXPOSURE"))
    if other:
        cost, chain_cost, shares, entry, state = other
        other_lots.append(lot(max(D(cost), D(chain_cost), D(shares) * D(entry)), state))
    own_lots = [lot(h * price, lot_state)] if h and lot_state else []
    policy = CapPolicy(max_per_market_micro=int(_CAP * 1_000_000))
    configure_global_allocator(
        RiskAllocator(policy, [*own_lots, *other_lots]),
        GovernorState(current_drawdown_pct=0.0, heartbeat_health=HeartbeatHealth.HEALTHY, ws_gap_active=False,
                      ws_gap_seconds=0, unknown_side_effect_count=0, reconcile_finding_count=0),
    )
    truth = D(AuctionCapitalAuthority(RiskAllocator(policy, other_lots)).capacity_usd(
        market_id="gamma", event_id="event", correlation_key=FAMILY_KEY,
    ))
    current = D(AuctionCapitalAuthority(RiskAllocator(policy, [*own_lots, *other_lots])).capacity_usd(
        market_id="gamma", event_id="event", correlation_key=FAMILY_KEY,
    ))
    monkeypatch.setattr(C, "_snapshot_row", lambda _c, _sid: {**_snapshot(), "condition_id": f"cond-{TOKEN}"})
    seen = []
    real_value = C.value_standing_entry

    def spy(*a, **k):
        seen.append(D(k["capital_limit_usd"]))
        return real_value(*a, **k)

    monkeypatch.setattr(C, "value_standing_entry", spy)
    rests = [r for r in C.find_open_entry_rests(conn) if r["command_id"] == "cmd"]
    C._capture_standing_entry_values(
        conn, sqlite3.connect(":memory:"), sqlite3.connect(":memory:"), rests,
        families={"cmd": FAMILY}, clock=lambda: at,
    )
    assert len(seen) == 1, "the pass must reach value_standing_entry"
    return current, seen[0], truth


# (case id, older_own, this_fill, row_has_fill, lot_state, other, label)
#   FIX: an earlier commit over-restores (headroom above truth) and fails this
#        case behaviourally (3f52be67b: every FIX; 997a86fce: older own lot).
#   PIN: every earlier commit already within [current, truth]; regression pin.
# With an older own lot, the order's share of the position row is not
# provable, so the own position counts as another order's and restoration
# stays at the current headroom (below truth, toward CANCEL).
_RESTORATION_MATRIX = [
    ("no_fill", False, "0", False, None, None, "PIN"),
    ("own_alone_published_confirmed", False, "2.6", True, "CONFIRMED_EXPOSURE", None, "PIN"),
    ("own_alone_published_optimistic", False, "2.6", True, "OPTIMISTIC_EXPOSURE", None, "PIN"),
    ("own_alone_unpublished", False, "2.6", False, None, None, "PIN"),
    ("older_own_fill_unpublished_row_without", True, "2.6", False, None, None, "FIX"),
    ("older_own_fill_unpublished_row_with", True, "2.6", True, None, None, "FIX"),
    ("older_own_fill_part_optimistic", True, "2.6", True, "OPTIMISTIC_EXPOSURE", None, "PIN"),
    ("older_own_fill_confirmed", True, "2.6", True, "CONFIRMED_EXPOSURE", None, "PIN"),
    ("other_confirmed_fill_unpublished", False, "2.6", False, None,
     ("1.5", "1.5", "3", "0.50", "CONFIRMED_EXPOSURE"), "FIX"),
    ("other_chain_cost_above_cost", False, "2.6", False, None,
     ("1.0", "2.0", "3", "0.50", "CONFIRMED_EXPOSURE"), "FIX"),
    ("other_shares_x_entry_above_cost", False, "2.6", False, None,
     ("1.0", "1.0", "4", "0.60", "CONFIRMED_EXPOSURE"), "FIX"),
    ("other_optimistic_fill_confirmed", False, "2.6", True, "CONFIRMED_EXPOSURE",
     ("1.3", "1.3", "2.6", "0.50", "OPTIMISTIC_EXPOSURE"), "PIN"),
    ("older_own_and_other_fill_unpublished", True, "2.6", False, None,
     ("1.5", "1.5", "3", "0.50", "CONFIRMED_EXPOSURE"), "FIX"),
]


class TestRestoredHeadroomNeverAboveTruePrefill:
    @pytest.mark.parametrize(
        "older_own,this_fill,row_has_fill,lot_state,other,label",
        [case[1:] for case in _RESTORATION_MATRIX],
        ids=[case[0] for case in _RESTORATION_MATRIX],
    )
    def test_headroom_is_within_current_and_true_prefill(
        self, monkeypatch, older_own, this_fill, row_has_fill, lot_state, other, label,
    ):
        current, restored, truth = _matrix_capture(
            monkeypatch, older_own=older_own, this_fill=this_fill, row_has_fill=row_has_fill,
            lot_state=lot_state, other=other,
        )
        assert restored <= truth, (label, current, restored, truth)
        assert restored >= min(current, truth), (label, current, restored, truth)

    @pytest.mark.parametrize("lot_state", [None, "CONFIRMED_EXPOSURE", "OPTIMISTIC_EXPOSURE"])
    def test_own_fill_alone_is_restored_exactly(self, monkeypatch, lot_state):
        # No older own lot, no other holding: the restoration must equal the
        # true pre-fill headroom (the D1 fill-split property depends on it).
        _current, restored, truth = _matrix_capture(
            monkeypatch, older_own=False, this_fill="2.6", row_has_fill=lot_state is not None,
            lot_state=lot_state, other=None,
        )
        assert restored == truth
