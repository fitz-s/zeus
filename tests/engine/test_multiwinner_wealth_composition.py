# Created: 2026-07-23
# Last reused/audited: 2026-07-23
# Authority basis: INV-44 serialized multiwinner wealth/reservation composition
"""INV-44 property antibodies for fresh wealth cuts between auction epochs."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from decimal import Decimal

import numpy as np
import pytest

from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
from src.engine.global_auction_universe import (
    current_portfolio_wealth_witness,
    probe_inflight_buy_ambiguity,
)
from src.solve import solver as solve
from src.state.collateral_ledger import init_collateral_schema
from src.state.portfolio import PortfolioState


_AT = dt.datetime(2026, 7, 23, 12, 0, tzinfo=dt.timezone.utc)


def _wealth_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_collateral_schema(conn)
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            token_id TEXT,
            side TEXT,
            size REAL,
            price REAL,
            intent_kind TEXT,
            state TEXT
        );
        CREATE TABLE venue_command_events (
            command_id TEXT,
            event_type TEXT,
            occurred_at TEXT
        );
        CREATE TABLE entry_exposure_obligations (
            command_id TEXT PRIMARY KEY,
            status TEXT,
            token_id TEXT,
            shares REAL,
            cost_basis_usd REAL,
            unbounded INTEGER,
            created_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots ("
        "pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,"
        "captured_at,authority_tier,raw_balance_payload_hash"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            25_000_000,
            1_000_000_000,
            2_000_000,
            json.dumps({}),
            json.dumps({}),
            0,
            json.dumps({}),
            _AT.isoformat(),
            "CHAIN",
            "inv-44-wallet",
        ),
    )
    return conn


def _witness(conn: sqlite3.Connection):
    return current_portfolio_wealth_witness(
        conn,
        decision_at_utc=_AT,
        max_age=dt.timedelta(seconds=30),
        portfolio_state=PortfolioState(
            authority="canonical_db",
            authority_scope="runtime_exposure",
        ),
    )


def test_family_joint_never_spends_fixed_fraction_above_kelly_target():
    """Minimum marketability cannot override the shared fractional-Kelly target."""
    family = "family-joint-weak-edge"
    candidate_id = "family-joint-weak-edge"
    token = "token-family-joint-weak-edge"
    condition = "condition-family-joint-weak-edge"
    captured_at = _AT - dt.timedelta(milliseconds=100)
    curve = ExecutableCostCurve(
        token_id=token,
        side="NO",
        snapshot_id="book-family-joint-weak-edge",
        book_hash="hash-family-joint-weak-edge",
        levels=(BookLevel(price=Decimal("0.78"), size=Decimal("57.5")),),
        fee_model=FeeModel(fee_rate=Decimal("0.05")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("5"),
        quote_ttl=dt.timedelta(seconds=1),
    )
    bindings = (
        solve.OutcomeTokenBinding(
            bin_id="bin",
            condition_id=condition,
            yes_token_id="yes-family-joint-weak-edge",
            no_token_id=token,
        ),
        solve.OutcomeTokenBinding(
            bin_id="other",
            condition_id="condition-family-joint-other",
            yes_token_id="yes-family-joint-other",
            no_token_id="no-family-joint-other",
        ),
    )
    payoff_q = 0.8125733672356523
    yes_samples = np.tile(np.array([1.0 - payoff_q, payoff_q]), (400, 1))
    witness_fields = {
        "family_key": family,
        "bindings": bindings,
        "q_version": "q-family-joint-weak-edge",
        "resolution_identity": "resolution-family-joint-weak-edge",
        "topology_identity": "topology-family-joint-weak-edge",
        "posterior_identity_hash": "posterior-family-joint-weak-edge",
        "source_truth_identity": "source-family-joint-weak-edge",
        "authority_certificate_hash": "certificate-family-joint-weak-edge",
        "band_alpha": 0.05,
        "band_basis": "joint_q_band_samples",
        "yes_point_q": np.mean(yes_samples, axis=0),
        "yes_q_samples": yes_samples,
        "captured_at_utc": captured_at,
    }
    witness = solve.JointOutcomeProbabilityWitness(
        **witness_fields,
        max_age=dt.timedelta(seconds=1),
        witness_identity=solve.joint_probability_witness_identity(**witness_fields),
    )
    candidate = solve.GlobalSingleOrderCandidate(
        candidate_id=candidate_id,
        family_key=family,
        bin_id="bin",
        condition_id=condition,
        side="NO",
        token_id=token,
        probability_witness_identity=witness.witness_identity,
        book_snapshot_id=curve.snapshot_id,
        book_captured_at_utc=captured_at,
        execution_curve_identity=solve.executable_curve_identity(curve),
        ledger_snapshot_id="ledger-current",
        executable_cost_curve=curve,
        resolution_identity=witness.resolution_identity,
    )
    endowment = solve.FamilyPortfolioEndowment(
        family_key=family,
        payout_by_bin_usd=tuple(
            (bin_id, Decimal("0")) for bin_id in witness.bin_ids
        ),
        current_token_shares=(),
        wealth_floor_usd=Decimal("465.531417"),
        spendable_cash_usd=Decimal("465.531417"),
        portfolio_capital_usd=Decimal("1450"),
        committed_capital_usd=Decimal("0"),
        ledger_snapshot_id="ledger-current",
    )

    full = solve.plan_family_joint_buy_targets(
        (candidate,),
        probability_witness=witness,
        endowment=endowment,
        capital_limit_by_candidate={candidate_id: Decimal("1450")},
        fractional_kelly_multiplier=Decimal("1"),
    )
    fractional = solve.plan_family_joint_buy_targets(
        (candidate,),
        probability_witness=witness,
        endowment=endowment,
        capital_limit_by_candidate={candidate_id: Decimal("1450")},
        fractional_kelly_multiplier=Decimal("0.03125"),
    )

    assert full.primary_candidate_id == candidate_id
    assert full.targets[0].shares > Decimal("55")
    assert full.targets[0].full_kelly_target_shares == full.targets[0].shares
    assert full.targets[0].full_kelly_target_shares * Decimal("0.03125") < Decimal("5")
    assert fractional.primary_candidate_id is None
    assert fractional.targets == ()
    assert fractional.no_trade_reason == "FAMILY_JOINT_NO_POSITIVE_TARGET"


def _record_bounded_winner(
    conn: sqlite3.Connection,
    *,
    epoch: int,
    amount_usd: Decimal,
) -> None:
    command_id = f"epoch-{epoch}"
    token_id = f"token-{epoch}"
    amount_micro = int(amount_usd * Decimal("1000000"))
    shares = amount_usd / Decimal("0.50")
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?)",
        (
            command_id,
            f"position-{epoch}",
            token_id,
            "BUY",
            float(shares),
            0.50,
            "ENTRY",
            "POST_ACKED",
        ),
    )
    conn.execute(
        "INSERT INTO entry_exposure_obligations VALUES (?,?,?,?,?,?,?)",
        (
            command_id,
            "OPEN",
            token_id,
            float(shares),
            float(amount_usd),
            0,
            _AT.isoformat(),
        ),
    )
    conn.execute(
        "INSERT INTO collateral_reservations ("
        "command_id,reservation_type,token_id,amount,created_at"
        ") VALUES (?,?,?,?,?)",
        (command_id, "PUSD_BUY", None, amount_micro, _AT.isoformat()),
    )


@pytest.mark.parametrize("stake_usd", [Decimal("6"), Decimal("10"), Decimal("12")])
def test_multiwinner_wealth_witness_strictly_decreases_until_cash_exhaustion(
    stake_usd: Decimal,
):
    conn = _wealth_conn()
    previous = _witness(conn)
    expected_epochs = int(previous.spendable_cash_usd // stake_usd)

    for epoch in range(expected_epochs):
        _record_bounded_winner(
            conn,
            epoch=epoch,
            amount_usd=stake_usd,
        )
        current = _witness(conn)
        assert current.spendable_cash_usd == previous.spendable_cash_usd - stake_usd
        assert current.spendable_cash_usd < previous.spendable_cash_usd
        assert current.reservations_usd == Decimal(epoch + 1) * stake_usd
        previous = current

    assert previous.spendable_cash_usd < stake_usd


def test_multiwinner_bounded_inflight_reservation_composes_and_unbounded_fails_closed():
    conn = _wealth_conn()
    _record_bounded_winner(
        conn,
        epoch=0,
        amount_usd=Decimal("10"),
    )

    bounded = _witness(conn)
    assert probe_inflight_buy_ambiguity(conn) is False
    assert bounded.spendable_cash_usd == Decimal("15")
    assert bounded.reservations_usd == Decimal("10")

    conn.execute(
        "INSERT INTO collateral_reservations ("
        "command_id,reservation_type,token_id,amount,created_at"
        ") VALUES (?,?,?,?,?)",
        ("unbounded", "PUSD_BUY", None, 5_000_000, _AT.isoformat()),
    )

    assert probe_inflight_buy_ambiguity(conn) is True
    with pytest.raises(ValueError, match="CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS"):
        _witness(conn)


def test_multiwinner_reactor_terminates_on_fresh_witness_cash_exhaustion():
    from src.events.reactor import EventSubmissionReceipt, GlobalBatchSubmitResult
    from tests.events.test_reactor import (
        _DT_VENUE_OPEN,
        _multiwinner_events,
        _multiwinner_reactor,
        _sequential_winner_batch,
        _store,
    )

    _world_conn, store = _store()
    events = _multiwinner_events("wealth-stop", 3)
    for event in events:
        store.insert_or_ignore(event)
    wealth_conn = _wealth_conn()
    stake = Decimal("10")
    batch_calls = 0
    submitted = 0

    def _batch(claimed, decision_time, *, claim_unpaged_winner=None):
        nonlocal batch_calls, submitted
        batch_calls += 1
        if _witness(wealth_conn).spendable_cash_usd < stake:
            return GlobalBatchSubmitResult(
                receipts={
                    event.event_id: EventSubmissionReceipt(
                        submitted=False,
                        event_id=event.event_id,
                        causal_snapshot_id=event.causal_snapshot_id,
                        reason="GLOBAL_AUCTION_NO_TRADE:CASH_DOMINATES",
                        proof_accepted=False,
                    )
                    for event in claimed
                },
                winner_event_id=None,
                venue_submit_count=0,
            )

        def _commit_winner(_winner) -> None:
            nonlocal submitted
            _record_bounded_winner(
                wealth_conn,
                epoch=submitted,
                amount_usd=stake,
            )
            submitted += 1

        return _sequential_winner_batch(
            claimed,
            decision_time,
            claim_unpaged_winner=claim_unpaged_winner,
            on_winner=_commit_winner,
        )

    reactor = _multiwinner_reactor(store, _batch)
    reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=None)

    assert submitted == 2
    assert batch_calls == 3
    assert _witness(wealth_conn).spendable_cash_usd == Decimal("5")
    assert _witness(wealth_conn).spendable_cash_usd < stake


def test_global_batch_accepts_bounded_inflight_but_rejects_unbounded_before_scope(
    monkeypatch,
):
    from src.engine import global_batch_runtime
    from tests.events.test_reactor import _DT_VENUE_OPEN, _forecast_event

    event = _forecast_event("inflight-composition", target_date="2026-05-25")
    bounded = _wealth_conn()
    _record_bounded_winner(
        bounded,
        epoch=0,
        amount_usd=Decimal("10"),
    )
    scope_reached = False

    def _scope_reached(**_kwargs):
        nonlocal scope_reached
        scope_reached = True
        raise RuntimeError("BOUNDED_INFLIGHT_REACHED_SCOPE")

    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        _scope_reached,
    )
    bounded_result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=_DT_VENUE_OPEN,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=bounded,
        payload_reader=lambda item: json.loads(item.payload_json),
        prepare_event=lambda *_: pytest.fail("scope probe stops before q preparation"),
        actuate_winner=lambda *_: pytest.fail("scope probe stops before actuation"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: _DT_VENUE_OPEN,
    )
    assert scope_reached is True
    assert "BOUNDED_INFLIGHT_REACHED_SCOPE" in bounded_result.receipts[event.event_id].reason

    unbounded = _wealth_conn()
    unbounded.execute(
        "INSERT INTO collateral_reservations ("
        "command_id,reservation_type,token_id,amount,created_at"
        ") VALUES (?,?,?,?,?)",
        ("unbounded", "PUSD_BUY", None, 5_000_000, _AT.isoformat()),
    )
    scope_reached = False
    unbounded_result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=_DT_VENUE_OPEN,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=unbounded,
        payload_reader=lambda item: json.loads(item.payload_json),
        prepare_event=lambda *_: pytest.fail("ambiguous inflight must fail before q"),
        actuate_winner=lambda *_: pytest.fail("ambiguous inflight must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: _DT_VENUE_OPEN,
    )
    assert scope_reached is False
    assert unbounded_result.receipts[event.event_id].reason.endswith(
        "CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS"
    )
