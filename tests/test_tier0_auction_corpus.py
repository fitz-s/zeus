# Created: 2026-09-27
# Last audited: 2026-09-28
# Authority basis: correction design review REQ-20260925-223704 §2 (every cut,
#   raw q before any rejection), §3 (complete ordered family simplex, quotes
#   stored with reasons, never patched), §10 (idempotent immutable identities,
#   conflicts fail visibly, the corpus never costs a receipt); 2026-09-28
#   review blockers (SQLITE_FULL lost the receipt; unbounded growth).
"""Complete auction learning corpus: behavioral acceptance.

Every test drives the real receipt writer (``_store_global_auction_receipt``),
the real post-commit flush (``_flush_tier0_learning_corpus``) or the real
fold/retention against a real SQLite DB, and reads rows back from the tables.
Nothing is recomputed from the fixture's own values.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import sys
import time
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

import src.engine.global_batch_runtime as gbr
import src.execution.post_trade_capital as ptc
from src.contracts.executable_cost_curve import BidBookLevel, BookLevel, ExecutableCostCurve, FeeModel
from src.contracts.strategy_capital_allocation import StrategyCapitalAllocationWitness
from src.engine import tier0_auction_corpus as corpus
from src.engine.global_auction_universe import (
    CurrentGlobalBookAsset,
    CurrentGlobalBookEpoch,
    current_global_book_epoch_identity,
)
from src.solve.solver import (
    GlobalSingleOrderCandidateEvaluation,
    GlobalSingleOrderDecision,
    JointOutcomeProbabilityWitness,
    OutcomeTokenBinding,
    joint_probability_witness_identity,
)
from src.state.schema.tier0_auction_corpus_schema import (
    SNAPSHOT_BOOK_FIELDS,
    ensure_tables,
)
from src.state.schema.tier0_candidate_set_provenance_schema import ensure_table

AT = _dt.datetime(2026, 9, 26, 12, 0, tzinfo=_dt.timezone.utc)
FAMILY = "edli_family_corpus_test"
# Deliberately NOT settlement order: witness bindings are condition-id sorted.
BINS = (("b-mid", 20.0, 20.0), ("b-low", None, 19.0), ("b-high", 21.0, None))
# b-mid q != 0.5 so a YES/NO swap or missing complement is observable.
YES_Q = (0.625, 0.125, 0.25)


@pytest.fixture(autouse=True)
def _empty_queue():
    corpus._PENDING.clear()
    corpus._OVERFLOW.clear()
    yield
    corpus._PENDING.clear()
    corpus._OVERFLOW.clear()


def _witness(*, yes_q=YES_Q, at=AT, family=FAMILY) -> JointOutcomeProbabilityWitness:
    bindings = tuple(
        OutcomeTokenBinding(
            bin_id=bin_id,
            condition_id=f"cond-{bin_id}",
            yes_token_id=f"yes-{bin_id}",
            no_token_id=f"no-{bin_id}",
        )
        for bin_id, _, _ in BINS
    )
    point = np.asarray(yes_q, dtype=np.float64)
    fields = dict(
        family_key=family,
        bindings=bindings,
        q_version="q-v1",
        resolution_identity="resolution",
        topology_identity="topology",
        posterior_identity_hash="posterior",
        source_truth_identity="source",
        authority_certificate_hash="certificate",
        band_alpha=0.05,
        band_basis="current-evidence",
        yes_point_q=point,
        yes_q_samples=np.tile(point, (400, 1)),
        captured_at_utc=at,
    )
    return JointOutcomeProbabilityWitness(
        **fields,
        max_age=_dt.timedelta(minutes=3),
        witness_identity=joint_probability_witness_identity(**fields),
    )


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("operator", ("v2", "v3", "resolver"))
def test_held_sell_point_kernel_trace_replays_active_mixture_without_samples(metric, operator):
    from src.calibration.day0_diurnal_residual import Day0DiurnalMixture
    from src.calibration.day0_resolver_terminal_residual import Day0ResolverTerminalInput
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.data import day0_hourly_vectors as hourly

    # Controlled arithmetic stress, not a claim of native source authority.
    rng = np.random.default_rng(20260929)
    semantics = SettlementSemantics("hko_daily", "C", 1.0, "oracle_truncate", "12:00:00Z")
    future = rng.uniform(28, 36, 51).tolist()
    final = rng.uniform(29, 37, 51).tolist() if operator != "v2" else []
    weights = rng.uniform(.1, 1, 51); weights /= weights.sum()
    scenarios = [(float(b), float(w)) for b, w in zip(rng.uniform(29, 33, 51), weights)]
    bounds = [(None, 19.)] + [(float(x), float(x)) for x in range(20, 40)] + [(40., None)]
    terminal = Day0ResolverTerminalInput(
        artifact_hash="a"*64, fit_cutoff_utc="2026-09-29T00:00:00+00:00",
        cell=(metric, "h12_18", "hko", "gap0", "HKO"),
        levels=tuple((f"L{i}|{metric}|hko|HKO", 1000, 7, 4.123456789+i) for i in range(4)),
        g_levels=((3, 2, 1, 1),)*4,
    )
    selected_operator = {
        "v2": hourly.DAY0_REMAINING_CARRIER_OPERATOR_V2,
        "v3": hourly.DAY0_REMAINING_CARRIER_OPERATOR_V3,
        "resolver": hourly.DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER,
    }[operator]
    if operator == "resolver": scenarios = [(31.173456789012345, 1.)]
    params = dict(
        future_extremes_c=future, final_extreme_centers_c=final,
        boundary_scenarios=scenarios, metric=metric,
        path_error_sigma_c=1.2345678901234567, instrument_sigma_c=.24567890123456789,
        bin_bounds_c=bounds, n_point=8192, n_samples=2, identity_inputs={"unit": "C"},
        settlement_semantics=semantics, operator=selected_operator,
        remaining_center_bias_native=.012345678901234567,
        resolver_terminal=terminal if operator == "resolver" else None,
    )
    carrier = hourly.build_day0_remaining_probability_carrier(**params)
    immutable = {**params,**{key:tuple(tuple(item) if isinstance(item,(tuple,list)) else item for item in params[key])
                            for key in ("future_extremes_c","final_extreme_centers_c","boundary_scenarios","bin_bounds_c")}}
    tuple_carrier = hourly.build_day0_remaining_probability_carrier(**immutable)
    assert tuple_carrier["content_identity"] == carrier["content_identity"]
    np.testing.assert_array_equal(tuple_carrier["q"],carrier["q"])
    np.testing.assert_array_equal(tuple_carrier["samples"],carrier["samples"])
    dead = tuple(i < 3 if metric == "high" else i >= len(bounds)-3 for i in range(len(bounds)))
    pi = np.asarray([0. if d else rng.uniform(.1, 1.) for d in dead]); pi /= pi.sum()
    mixture = Day0DiurnalMixture(.4312345678901234, tuple(pi), dead, -1, 32.34567890123456,
                                "2026-09-29", "controlled-active-"+"b"*64)
    final_q = mixture.apply(carrier["q"])
    kernel = {key: value for key, value in params.items()
              if key not in {"n_point", "n_samples", "identity_inputs", "settlement_semantics", "resolver_terminal"}}
    kernel.update(settlement=dict(resolution_source="hko_daily", measurement_unit="C", precision=1.,
                                  rounding_rule="oracle_truncate", finalization_time="12:00:00Z"),
                  resolver_terminal=terminal.to_payload() if operator == "resolver" else None,
                  carrier_to_witness=list(range(len(bounds))), base_yes_q=carrier["q"])
    trace = dict(schema="held_sell_point_kernel_trace_v1", status="READY", lane="HELD_MONITOR",
                 decision_at_utc="2026-09-30T06:20:00+00:00", loaded_revision="a"*40,
                 family="Hong Kong|2026-09-30|"+metric, kernel=kernel, diurnal=mixture.to_payload(),
                 final_yes_q=final_q, producer_witness_identity="b"*64,
                 probability_content_identity="c"*64, source_truth_identity="d"*64,
                 posterior_identity_hash="e"*64, q_version="day0-v28",
                 bindings=[[str(i), f"cond-{i}", f"yes-{i}", f"no-{i}"] for i in range(len(bounds))],
                 producer_identity_recipe=dict(kind="joint_v1",resolution_identity="r"*64,
                     topology_identity="t"*64,authority_certificate_hash="a"*64,band_alpha=.05,
                     band_basis="current-evidence",captured_at_utc="2026-09-30T06:20:00+00:00",
                     sample_shape=[400,len(bounds)],sample_matrix_identity="s"*64))
    frozen = corpus.freeze_held_sell_point_trace(trace)
    future[0] = -999.  # Async consumers must not close over a mutable array.
    np.testing.assert_array_equal(corpus.replay_held_sell_point_trace(frozen), final_q)
    np.testing.assert_array_equal(1-np.asarray(corpus.replay_held_sell_point_trace(frozen)), 1-np.asarray(final_q))
    assert len(frozen) <= 16*1024
    assert len(corpus.encode_payload(frozen)) <= 8*1024


def _curve(token: str, side: str, ask: str) -> ExecutableCostCurve:
    return ExecutableCostCurve(
        token_id=token,
        side=side,
        snapshot_id=f"snap-{token}",
        book_hash=f"hash-{token}",
        levels=(BookLevel(price=Decimal(ask), size=Decimal("100")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"),
        quote_ttl=_dt.timedelta(seconds=30),
        fee_details={
            "feeSchedule_taker_only": True,
            "fee_rate_bps": 0.0,
            "fee_rate_fraction": 0.0,
            "fee_rate_raw_unit": "fraction",
            "fee_rate_source_field": "fee_rate_fraction",
            "fee_type": "weather_fees",
            "source": "test",
            "token_id": token,
        },
    )


# (bin, side) -> (status, bid, ask). b-low YES has neither ask nor bid;
# b-high is not executable on either side and generates no leg at all.
BOOK = {
    ("b-mid", "YES"): ("EXECUTABLE", "0.45", "0.55"),
    ("b-mid", "NO"): ("EXECUTABLE", "0.44", "0.56"),
    ("b-low", "YES"): ("NO_ASK", None, None),
    ("b-low", "NO"): ("EXECUTABLE", "0.70", "0.80"),
    ("b-high", "YES"): ("VENUE_NOT_EXECUTABLE", None, None),
    ("b-high", "NO"): ("VENUE_NOT_EXECUTABLE", None, None),
}
CROSSED_BOOK = {**BOOK, ("b-mid", "YES"): ("EXECUTABLE", "0.60", "0.55")}


def _book_epoch(book=BOOK, at=AT) -> CurrentGlobalBookEpoch:
    states, assets = [], []
    for (bin_id, side), (status, bid, ask) in book.items():
        token = f"{side.lower()}-{bin_id}"
        states.append(
            (FAMILY, bin_id, f"cond-{bin_id}", side, token, status,
             f"hash-{token}", "event", "gamma", "False")
        )
        if ask is not None:
            assets.append(
                CurrentGlobalBookAsset(
                    family_key=FAMILY, bin_id=bin_id, condition_id=f"cond-{bin_id}",
                    gamma_market_id="gamma", market_event_id="event", side=side,
                    token_id=token, curve=_curve(token, side, ask),
                    captured_at_utc=at, neg_risk=False,
                    bid_levels=(
                        (BidBookLevel(price=Decimal(bid), size=Decimal("100")),)
                        if bid is not None else ()
                    ),
                )
            )
    return CurrentGlobalBookEpoch(
        assets=tuple(assets),
        asset_states=tuple(states),
        captured_at_utc=at,
        max_age=_dt.timedelta(seconds=30),
        witness_identity=current_global_book_epoch_identity(
            asset_states=tuple(states), captured_at_utc=at
        ),
    )


def _rejected(bin_id: str, side: str, witness, reason: str, mode="TAKER_LIMIT"):
    return GlobalSingleOrderCandidateEvaluation(
        candidate_id=f"{side}:{bin_id}:{mode}",
        family_key=FAMILY, bin_id=bin_id, condition_id=f"cond-{bin_id}",
        side=side, token_id=f"{side.lower()}-{bin_id}", action="BUY",
        status="REJECTED", rejection_reason=reason, execution_mode=mode,
        fill_probability=1.0 if mode == "TAKER_LIMIT" else 0.5,
        fill_probability_source="immediate_taker" if mode == "TAKER_LIMIT" else "maker_model",
        rest_deadline_minutes=None if mode == "TAKER_LIMIT" else 10.0,
        probability_witness_identity=witness.witness_identity,
    )


def _no_winner_decision(witness):
    evaluations = (
        _rejected("b-mid", "YES", witness, "NON_POSITIVE_EXPECTED_OBJECTIVE"),
        _rejected("b-mid", "NO", witness, "LIVE_UNIT_PRICE_OUT_OF_BOUNDS"),
        # Duplicate NO leg (same bin/side, maker mode): one fit unit, two legs.
        _rejected("b-mid", "NO", witness, "FAMILY_JOINT_NO_POSITIVE_TARGET", "MAKER_REST"),
        _rejected("b-low", "NO", witness, "GLOBAL_ENTRY_FEASIBILITY_BID_INVALID"),
    )
    return GlobalSingleOrderDecision(
        candidate=None, shares=Decimal("0"), cost_usd=Decimal("0"),
        robust_delta_log_wealth=0.0, robust_ev_usd=0.0, capital_efficiency=0.0,
        no_trade_reason="NO_CURRENT_EXECUTABLE_POSITIVE_ORDER",
        rejection_reasons={e.candidate_id: e.rejection_reason for e in evaluations},
        candidate_evaluations=evaluations,
        candidate_input_count=len(evaluations),
    )


def _wealth():
    return SimpleNamespace(
        witness_identity="wealth", economic_identity="wealth-econ",
        ledger_snapshot_id="ledger", position_set_hash="positions",
        wealth_floor_usd=Decimal("18"), wealth_ceiling_usd=Decimal("22"),
        spendable_cash_usd=Decimal("10"), reservations_usd=Decimal("2"),
        collateral_authority="TEST_CHAIN",
        strategy_capital_allocation=StrategyCapitalAllocationWitness.build(
            capital_basis_usd=Decimal("20"), committed_capital_usd=Decimal("0"),
            venue_spendable_cash_usd=Decimal("10"), allocation={"mode": "wallet_total"},
        ),
    )


def _trade_db(tmp_path, name="trade.db") -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / name)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL,
            started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
            artifact_json TEXT NOT NULL, timestamp TEXT NOT NULL, env TEXT NOT NULL
        )
        """
    )
    ensure_table(conn)
    ensure_tables(conn)
    conn.commit()
    return conn


def _store(conn, decision, *, epoch="epoch-1", witness=None, book=None, at=AT, flush=True, point_traces=()):
    """Receipt write (committed) followed by the batch's post-commit flush."""

    witness = witness or _witness()
    book = book or _book_epoch()
    row_id = gbr._store_global_auction_receipt(
        conn,
        selected=SimpleNamespace(decision=decision),
        selection_epoch_identity=epoch,
        selection_cut_at_utc=at,
        decision_at_utc=at + _dt.timedelta(seconds=1),
        probability_manifest=((FAMILY, witness.witness_identity),),
        full_scope_identity="scope",
        full_scope_family_keys=(FAMILY,),
        probability_ineligible_by_family={},
        book_epoch_identity=book.witness_identity,
        book_asset_count=len(book.assets),
        book_asset_states=book.asset_states,
        wealth_witness=_wealth(),
        fractional_kelly_multiplier=Decimal("0.25"),
        book_captured_at_utc=book.captured_at_utc,
        book_max_age=book.max_age,
        family_context_by_key={
            FAMILY: {"city": "London", "target_date": "2026-09-26", "metric": "high"}
        },
        probability_witnesses={FAMILY: witness},
        book_epoch=book,
        buy_candidates_enabled=True,
        held_point_traces=point_traces,
    )
    conn.commit()
    if flush:
        _flush(conn)
    return row_id


def _flush(conn) -> int:
    return gbr._flush_tier0_learning_corpus(
        conn,
        connection_key=gbr._decision_log_connection_key(conn),
        work_context=None,
    )


def _count(conn, table) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _only(conn, table):
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    assert len(rows) == 1, (table, len(rows))
    return rows[0]


def _snapshot(conn):
    return corpus.decode_payload(_only(conn, "tier0_family_snapshot")["payload"])


def _book_rows(conn):
    topology = corpus.decode_payload(_only(conn, "tier0_family_topology")["payload"])
    book = _snapshot(conn)["book"]
    return {
        binding[0]: dict(zip(SNAPSHOT_BOOK_FIELDS, row))
        for binding, row in zip(topology["bindings"], book)
    }


def _built(witness, decision, *, epoch="epoch-x", reason="R", at=AT, book=None):
    return gbr._tier0_cut_corpus(
        selection_epoch_identity=epoch, reason=reason, decision_at_utc=at,
        scope_family_count=1, probability_witnesses={FAMILY: witness},
        ineligible_by_family={}, excluded_by_family={},
        evaluations=decision.candidate_evaluations, winner_candidate_id=None,
        book_epoch=book or _book_epoch(), family_context_by_key={},
        fractional_kelly_multiplier=Decimal("0.25"), buy_candidates_enabled=True,
    )


def _controlled_point_trace():
    """Typed math/corpus seam fixture, not a native source authority claim."""
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.data.day0_hourly_vectors import (
        build_day0_remaining_probability_carrier, DAY0_REMAINING_CARRIER_OPERATOR_V2,
    )
    semantics = SettlementSemantics("hko_daily","C",1.,"oracle_truncate","12:00:00Z")
    params = dict(future_extremes_c=(19.5,20.5,21.5),final_extreme_centers_c=(),
        boundary_scenarios=((18.2,1.),),metric="high",path_error_sigma_c=.31,
        instrument_sigma_c=.22,bin_bounds_c=tuple((lo,hi) for _,lo,hi in BINS),
        remaining_center_bias_native=0.,operator=DAY0_REMAINING_CARRIER_OPERATOR_V2)
    carrier = build_day0_remaining_probability_carrier(**params,n_point=100,n_samples=1,
        identity_inputs={"unit":"C"},settlement_semantics=semantics)
    witness = _witness(yes_q=carrier["q"])
    bindings = [[b.bin_id,b.condition_id,b.yes_token_id,b.no_token_id] for b in witness.bindings]
    trace = dict(schema="held_sell_point_kernel_trace_v1",status="READY",lane="ENTRY",
        role="SELECTED_GLOBAL",selected_lane="ENTRY",family=witness.family_key,
        producer_witness_identity=witness.witness_identity,consumer_witness_identity=witness.witness_identity,
        probability_content_identity=witness.probability_content_identity,q_version=witness.q_version,
        source_truth_identity=witness.source_truth_identity,posterior_identity_hash=witness.posterior_identity_hash,
        bindings=bindings,consumer_bindings=bindings,final_yes_q=witness.yes_point_q.tolist(),
        consumer_yes_q=witness.yes_point_q.tolist(),diurnal=None,
        kernel={**params,"base_yes_q":carrier["q"],"carrier_to_witness":[0,1,2],
                "settlement":dict(resolution_source="hko_daily",measurement_unit="C",precision=1.,
                    rounding_rule="oracle_truncate",finalization_time="12:00:00Z")})
    return witness,corpus.freeze_held_sell_point_trace(trace)


@pytest.mark.parametrize("fault",("none","producer_id","rebound_id","content","point","samples","token","shape","source"))
def test_held_point_trace_compact_recipe_binds_original_and_completed_consumer(fault):
    from dataclasses import replace
    from src.engine import event_reactor_adapter as era
    from src.engine.global_auction_universe import _rebind_probability_witness_tokens

    witness,raw = _controlled_point_trace()
    original_bindings = tuple(replace(binding,no_token_id=None) for binding in witness.bindings)

    def with_identity(value,**changes):
        fields = {key:getattr(value,key) for key in (
            "family_key","bindings","q_version","resolution_identity","topology_identity",
            "posterior_identity_hash","source_truth_identity","authority_certificate_hash",
            "band_alpha","band_basis","yes_point_q","yes_q_samples","captured_at_utc",
        )}
        fields.update(changes)
        return replace(value,**changes,witness_identity=joint_probability_witness_identity(**fields))

    original = with_identity(witness,bindings=original_bindings)
    trace = json.loads(raw)
    capture = {"kernel":json.dumps(trace["kernel"]).encode(),
               "base_yes_q":json.dumps(original.yes_point_q.tolist()).encode(),
               "carrier_content_identity":b"controlled-point-kernel"}
    frozen = era._freeze_prepared_held_point_trace(capture,SimpleNamespace(probability_witness=original),
        lane="ENTRY",at=AT)
    trace = json.loads(frozen)
    assert trace["status"] == "READY"
    assert era._held_point_trace_matches_consumer(trace,original)
    consumer = _rebind_probability_witness_tokens(original,
        token_map_by_condition={binding.condition_id:(binding.yes_token_id,binding.no_token_id)
                                for binding in witness.bindings},
        required_token_ids=frozenset(token for binding in witness.bindings
                                    for token in (binding.yes_token_id,binding.no_token_id)))
    assert consumer.witness_identity != original.witness_identity
    assert consumer.yes_q_samples is original.yes_q_samples
    assert era._held_point_trace_matches_consumer(trace,consumer)
    if fault == "producer_id": trace["producer_witness_identity"] = "0"*64
    elif fault == "rebound_id": consumer = with_identity(consumer,captured_at_utc=AT+_dt.timedelta(seconds=1))
    elif fault == "content": trace["probability_content_identity"] = "0"*64
    elif fault == "point":
        trace["final_yes_q"][0] += .01; trace["final_yes_q"][1] -= .01
    elif fault == "samples":
        samples = consumer.yes_q_samples.copy()
        samples[0,0] += .01; samples[0,1] -= .01
        consumer = with_identity(consumer,yes_q_samples=samples)
    elif fault == "token":
        # Completion can fill NO, but cannot replace an existing YES token.
        consumer = with_identity(consumer,bindings=(replace(consumer.bindings[0],yes_token_id="wrong"),
                                                    *consumer.bindings[1:]))
    elif fault == "shape": trace["producer_identity_recipe"]["sample_shape"][0] += 1
    elif fault == "source": trace["source_truth_identity"] = "different-source"
    assert era._held_point_trace_matches_consumer(trace,consumer) is (fault=="none")
    assert set(trace["producer_identity_recipe"]) == {
        "kind","resolution_identity","topology_identity","authority_certificate_hash",
        "band_alpha","band_basis","captured_at_utc","sample_shape","sample_matrix_identity",
    }
    assert "yes_q_samples" not in frozen.decode()


@pytest.mark.parametrize("status",("READY","UNAVAILABLE"))
def test_held_point_trace_recipe_rejects_sample_values_even_when_unavailable(status):
    witness,raw = _controlled_point_trace()
    trace = json.loads(raw)
    trace.update(status=status,producer_identity_recipe={"yes_q_samples":witness.yes_q_samples.tolist()})
    unavailable = json.loads(corpus.freeze_held_sell_point_trace(trace))
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["reason"] == "PRODUCER_IDENTITY_RECIPE_FIELDS_INVALID"
    assert "producer_identity_recipe" not in unavailable
    assert unavailable["producer_witness_identity"] == witness.witness_identity


def test_held_point_trace_sidecar_preserves_receipt_q_books_legs_and_old_v1(tmp_path):
    witness,trace = _controlled_point_trace()
    decision = _no_winner_decision(witness)
    baseline = _trade_db(tmp_path,"baseline.db")
    observed = _trade_db(tmp_path,"observed.db")
    _store(baseline,decision,witness=witness)
    _store(observed,decision,witness=witness,point_traces=(trace,),flush=False)
    pending,_ = corpus.pending_cuts(gbr._decision_log_connection_key(observed))
    assert pending[0]._diagnostic_charge == sys.getsizeof((trace,))+sys.getsizeof(trace)
    assert corpus.point_trace_pending_charge() == pending[0]._diagnostic_charge
    _flush(observed)
    assert corpus.point_trace_pending_charge() == 0
    base_state = _snapshot(baseline)
    traced_state = _snapshot(observed)
    retained = traced_state.pop("held_sell_point_traces")
    assert traced_state == base_state
    np.testing.assert_array_equal(corpus.replay_held_sell_point_trace(
        corpus.freeze_held_sell_point_trace(retained[0])),witness.yes_point_q)
    assert _only(baseline,"tier0_family_snapshot")["payload_encoding"] == corpus.SNAPSHOT_ENCODING
    assert _only(observed,"tier0_family_snapshot")["payload_encoding"] == corpus.SNAPSHOT_POINT_TRACE_ENCODING
    for table in ("decision_log","tier0_auction_cut"):
        def immutable_rows(conn):
            return [{key:row[key] for key in row.keys() if key not in {"timestamp","created_at"}}
                    for row in conn.execute(f"SELECT * FROM {table}")]
        assert immutable_rows(baseline) == immutable_rows(observed)
    with pytest.raises(ValueError,match="HELD_POINT_TRACE_UNAVAILABLE"):
        corpus.replay_held_sell_point_trace(corpus._point_trace_unavailable("LEGACY_V1_NO_TRACE"))


@pytest.mark.parametrize("fault",("binding","codec","canonical","unavailable","overflow"))
def test_held_point_trace_fault_drops_only_optional_diagnostic(tmp_path,monkeypatch,fault):
    witness,trace = _controlled_point_trace()
    decision = _no_winner_decision(witness)
    base = _built(witness,decision)
    if fault == "binding":
        bad = json.loads(trace); bad["consumer_bindings"][0][2] = "different-token"
        trace = corpus.freeze_held_sell_point_trace(bad)
    elif fault == "unavailable":
        trace = corpus._point_trace_unavailable("UNSUPPORTED_POINT_KERNEL_V1",json.loads(trace))
    pending = corpus.PendingCut(lambda:(base,()),1,point_traces=(trace,))
    corpus.queue_cut("db-one",pending)
    if fault == "codec":
        monkeypatch.setattr(corpus,"encode_payload",lambda _: (_ for _ in ()).throw(RuntimeError("codec")))
        monkeypatch.setattr(corpus._LOG,"warning",lambda *_: (_ for _ in ()).throw(RuntimeError("logger")))
    elif fault == "canonical":
        monkeypatch.setattr(corpus,"_canonical",lambda _: (_ for _ in ()).throw(RuntimeError("json")))
        assert json.loads(corpus.freeze_held_sell_point_trace(json.loads(trace)))["status"] == "UNAVAILABLE"
    elif fault == "overflow":
        monkeypatch.setattr(corpus,"_POINT_TRACE_QUEUE_LIMIT",1)
        corpus.queue_cut("db-two",corpus.PendingCut(lambda:(base,()),2,point_traces=(trace,)))
        assert corpus.pending_cuts("db-two")[0][0]._diagnostic_charge == 0
    rows,_ = pending.rows()
    assert rows.cut_row == base.cut_row
    assert rows.q_raw_by_candidate == base.q_raw_by_candidate
    if fault not in {"unavailable","overflow"}:
        assert rows.families == base.families
    assert pending._point_traces == ()
    assert pending._diagnostic_charge <= sys.getsizeof((trace,))+sys.getsizeof(trace)
    corpus.release_cuts("db-one",(pending,),0)
    corpus.release_cuts("db-one",(pending,),0)
    assert corpus.point_trace_pending_charge() == 0


def test_held_point_trace_queue_counts_real_forms_across_databases_and_releases_cancel(monkeypatch):
    witness,trace = _controlled_point_trace()
    base = _built(witness,_no_winner_decision(witness))
    first = corpus.PendingCut(lambda:(base,()),1,point_traces=(trace,))
    charge = first._diagnostic_charge
    monkeypatch.setattr(corpus,"_POINT_TRACE_QUEUE_LIMIT",charge)
    corpus.queue_cut("first",first)
    second = corpus.PendingCut(lambda:(base,()),2,point_traces=(trace,))
    corpus.queue_cut("second",second)
    assert second._diagnostic_charge == 0
    assert corpus.point_trace_pending_charge() == charge
    rows,_ = first.rows()
    assert first._point_traces == ()
    expected = sum(max(0,sys.getsizeof(new.snapshot[5])-sys.getsizeof(old.snapshot[5]))
                   for new,old in zip(rows.families,base.families))
    assert corpus.point_trace_pending_charge() == expected
    corpus.release_cuts("first",(first,),0)
    assert corpus.point_trace_pending_charge() == 0
    def cancelled(): raise KeyboardInterrupt("cancelled builder")
    cancelled_cut = corpus.PendingCut(cancelled,3,point_traces=(trace,))
    corpus.queue_cut("cancel",cancelled_cut)
    assert cancelled_cut._diagnostic_charge > 0
    with pytest.raises(KeyboardInterrupt): cancelled_cut.rows()
    assert cancelled_cut._point_traces == () and cancelled_cut._diagnostic_charge == 0
    assert corpus.point_trace_pending_charge() == 0
    corpus._PENDING.clear()  # Process restart loses only pending diagnostics/cuts.
    assert corpus.point_trace_pending_charge() == 0


@pytest.mark.parametrize("fault",("v1","nonfinite","oversized","projection","base","mixture"))
def test_held_point_trace_replay_refuses_uncaptured_or_unavailable_kernel(fault):
    from src.data.day0_hourly_vectors import DAY0_REMAINING_CARRIER_OPERATOR_V1
    _,raw = _controlled_point_trace()
    trace = json.loads(raw)
    if fault == "v1": trace["kernel"]["operator"] = DAY0_REMAINING_CARRIER_OPERATOR_V1
    elif fault == "nonfinite": trace["kernel"]["future_extremes_c"][0] = float("nan")
    elif fault == "oversized": trace["kernel"]["future_extremes_c"] = [31.123456789]*2000
    elif fault == "projection": trace["kernel"]["carrier_to_witness"] = [0,0,2]
    elif fault == "base": trace["kernel"]["base_yes_q"][0] += .01
    elif fault == "mixture": trace["final_yes_q"][0] += .01
    frozen = corpus.freeze_held_sell_point_trace(trace)
    assert len(frozen) <= 16*1024 and len(corpus.encode_payload(frozen)) <= 8*1024
    if fault in {"v1","nonfinite","oversized"}:
        assert json.loads(frozen)["status"] == "UNAVAILABLE"
    with pytest.raises(ValueError,match="HELD_POINT_TRACE_"):
        corpus.replay_held_sell_point_trace(frozen)


def test_held_point_trace_transaction_cap_and_queue_overflow_do_not_expand_existing_limits(monkeypatch):
    witness,trace = _controlled_point_trace()
    base = _built(witness,_no_winner_decision(witness))
    pending = corpus.PendingCut(lambda:(base,()),1,point_traces=(trace,))
    observed,_ = pending.rows()
    family = observed.families[0]
    size = len(family.snapshot[5])
    monkeypatch.setattr(corpus,"_POINT_TRACE_TRANSACTION_LIMIT",size+1)
    # Stress the pure chunker with three legal encoded bodies; no source or
    # authority claim is inferred from these repeated fixture rows.
    wide = corpus.CutCorpus(observed.cut_row,(family,)*3,observed.q_raw_by_candidate)
    chunks = []
    monkeypatch.setattr(corpus,"_write_family_content",lambda _conn,rows: chunks.append(rows))
    monkeypatch.setattr(corpus,"write_cut",lambda *_args,**_kwargs: None)
    for unit in corpus.cut_write_units(object(),wide,decision_log_id=1,max_new_rows=32): unit()
    assert [len(chunk) for chunk in chunks] == [1,1,1]
    assert all(sum(len(row.snapshot[5]) for row in chunk)<=size+1 for chunk in chunks)
    assert all(len(chunk)<=32 for chunk in chunks)
    monkeypatch.setattr(corpus,"_QUEUE_LIMIT",1)
    first = corpus.PendingCut(lambda:(base,()),2,point_traces=(trace,))
    second = corpus.PendingCut(lambda:(base,()),3,point_traces=(trace,))
    corpus.queue_cut("overflow",first); corpus.queue_cut("overflow",second)
    assert first._diagnostic_charge == 0 and first._point_traces == ()
    assert corpus.pending_cuts("overflow") == ((second,),1)
    assert corpus.point_trace_pending_charge() == second._diagnostic_charge


def test_no_winner_cut_persists_cut_row_and_every_leg_with_raw_q(tmp_path):
    conn = _trade_db(tmp_path)
    witness = _witness()
    row_id = _store(conn, _no_winner_decision(witness), witness=witness)

    cut = _only(conn, "tier0_auction_cut")
    assert cut["status"] == "NO_TRADE"
    assert cut["reason"] == "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER"
    assert cut["selection_epoch_identity"] == "epoch-1"
    assert cut["decision_log_id"] == row_id
    assert cut["candidate_count"] == 4
    assert (cut["full_scope_family_count"], cut["eligible_family_count"]) == (1, 1)
    link = _only(conn, "tier0_cut_family")
    assert link["probability_witness_identity"].hex() == witness.witness_identity
    assert corpus.decode_payload(cut["payload"])["policy"]["fractional_kelly_multiplier"] == "0.25"
    # Every rejected leg with its reason; bins are witness column indexes.
    legs = _snapshot(conn)["legs"]
    assert sorted((leg[0], leg[1], leg[3], leg[5]) for leg in legs) == sorted([
        (0, "YES", "TAKER_LIMIT", "NON_POSITIVE_EXPECTED_OBJECTIVE"),
        (0, "NO", "TAKER_LIMIT", "LIVE_UNIT_PRICE_OUT_OF_BOUNDS"),
        (0, "NO", "MAKER_REST", "FAMILY_JOINT_NO_POSITIVE_TARGET"),
        (1, "NO", "TAKER_LIMIT", "GLOBAL_ENTRY_FEASIBILITY_BID_INVALID"),
    ])
    # The winner-only per-candidate table stays winner-only (frozen population).
    assert _count(conn, "tier0_candidate_set_provenance") == 0


def test_family_row_carries_all_k_bins_and_simplex_sums_to_one(tmp_path):
    conn = _trade_db(tmp_path)
    witness = _witness()
    _store(conn, _no_winner_decision(witness), witness=witness)

    snapshot_row = _only(conn, "tier0_family_snapshot")
    topology = corpus.decode_payload(_only(conn, "tier0_family_topology")["payload"])
    snapshot = corpus.decode_payload(snapshot_row["payload"])
    # b-high has no generated leg at all, yet its column is archived.
    assert [b[0] for b in topology["bindings"]] == [b for b, _, _ in BINS]
    assert snapshot["raw_yes_q"] == list(YES_Q)
    assert len(snapshot["book"]) == len(BINS) == 3
    assert sum(snapshot["raw_yes_q"]) == pytest.approx(1.0, abs=1e-9)
    assert snapshot_row["simplex_complete"] == 1
    assert _only(conn, "tier0_family_topology")["native_unit"] == "C"
    book = _book_rows(conn)
    assert book["b-mid"]["yes_mid"] == "0.5"
    assert book["b-high"]["yes_mid_unavailable_reason"] == "VENUE_NOT_EXECUTABLE"


def test_no_leg_recovers_yes_probability_as_complement():
    witness = _witness()
    held_q = corpus.candidate_raw_q({FAMILY: witness}, _rejected("b-mid", "NO", witness, "X"))
    yes_q = corpus.candidate_raw_q({FAMILY: witness}, _rejected("b-mid", "YES", witness, "X"))
    assert held_q == pytest.approx(0.375)  # 1 - 0.625, pinned literally
    assert 1.0 - held_q == pytest.approx(yes_q)
    # A leg bound to a different witness identity gets no q rather than a guess.
    stale = _rejected("b-mid", "NO", _witness(yes_q=(0.5, 0.25, 0.25)), "X")
    assert corpus.candidate_raw_q({FAMILY: witness}, stale) is None


def test_winner_cut_candidate_rows_take_raw_q_from_the_witness(tmp_path):
    conn = _trade_db(tmp_path)
    witness = _witness()
    decision = _no_winner_decision(witness)
    built = _built(witness, decision)
    rows = gbr._tier0_candidate_rows(
        evaluations=decision.candidate_evaluations,
        selection_epoch_identity="epoch-w",
        decision_at_utc=AT,
        family_context_by_key={FAMILY: {"city": "London", "target_date": "2026-09-26"}},
        q_raw_by_candidate=built.q_raw_by_candidate,
    )
    gbr._insert_tier0_candidate_rows(conn, rows)
    stored = conn.execute(
        "SELECT side, bin_id, q_raw FROM tier0_candidate_set_provenance"
    ).fetchall()
    assert len(stored) == 4 and all(row["q_raw"] is not None for row in stored)
    for row in stored:
        yes = YES_Q[[b for b, _, _ in BINS].index(row["bin_id"])]
        assert row["q_raw"] == pytest.approx(yes if row["side"] == "YES" else 1.0 - yes)


def test_duplicate_legs_do_not_create_duplicate_fit_rows(tmp_path):
    conn = _trade_db(tmp_path)
    witness = _witness()
    _store(conn, _no_winner_decision(witness), witness=witness, epoch="epoch-1")
    # A later cut with identical family content (new witness clock, same q,
    # same book, same legs) reuses the family state: one fit row, two cuts.
    later = AT + _dt.timedelta(seconds=10)
    witness2 = _witness(at=later)
    assert witness2.witness_identity != witness.witness_identity
    _store(conn, _no_winner_decision(witness2), witness=witness2, epoch="epoch-2",
           at=later, book=_book_epoch(at=later))
    assert _count(conn, "tier0_auction_cut") == 2
    assert _count(conn, "tier0_cut_family") == 2
    assert _count(conn, "tier0_family_snapshot") == 1
    assert _count(conn, "tier0_family_topology") == 1
    # One family state holds one YES/NO book row per bin however many legs:
    # the b-mid NO leg appears twice (taker + maker) but its bin is one column.
    assert len(_snapshot(conn)["book"]) == len(BINS)


def test_identical_retry_is_idempotent(tmp_path):
    conn = _trade_db(tmp_path)
    witness = _witness()
    built = _built(witness, _no_winner_decision(witness))
    for _ in range(2):
        corpus.write_cut(conn, built, decision_log_id=7)
    conn.commit()
    assert _count(conn, "tier0_auction_cut") == 1
    assert _count(conn, "tier0_cut_family") == 1
    assert _count(conn, "tier0_family_snapshot") == 1


def test_conflicting_cut_payload_fails_visibly(tmp_path):
    conn = _trade_db(tmp_path)
    witness = _witness()
    decision = _no_winner_decision(witness)
    corpus.write_cut(conn, _built(witness, decision, reason="A"), decision_log_id=1)
    conflicting = gbr._tier0_cut_corpus(
        selection_epoch_identity="epoch-x", reason="A", decision_at_utc=AT,
        scope_family_count=1, probability_witnesses={FAMILY: witness},
        ineligible_by_family={"other": "X"}, excluded_by_family={},
        evaluations=decision.candidate_evaluations, winner_candidate_id=None,
        book_epoch=_book_epoch(), family_context_by_key={},
        fractional_kelly_multiplier=Decimal("0.25"), buy_candidates_enabled=True,
    )
    with pytest.raises(corpus.Tier0CorpusIdentityConflict):
        corpus.write_cut(conn, conflicting, decision_log_id=1)


def test_flush_fault_keeps_the_receipt_and_retries_the_cut(tmp_path):
    conn = _trade_db(tmp_path)
    witness = _witness()
    decision = _no_winner_decision(witness)
    _store(conn, decision, witness=witness)
    # Forge a conflicting stored payload for the same immutable cut identity.
    conn.execute("UPDATE tier0_auction_cut SET payload_sha256 = 'forged'")
    conn.commit()
    before = _count(conn, "decision_log")
    _store(conn, decision, witness=witness)
    assert _count(conn, "decision_log") == before + 1
    assert _only(conn, "tier0_auction_cut")["payload_sha256"] == "forged"
    # The conflicting cut stays queued for a later flush; nothing was dropped.
    key = gbr._decision_log_connection_key(conn)
    assert len(corpus.pending_cuts(key)[0]) == 1


def test_real_sqlite_full_keeps_the_receipt_and_queues_the_cut(tmp_path):
    """Review blocker: SQLITE_FULL inside the corpus INSERT auto-rolled back the
    receipt transaction. The corpus now writes in its own transaction after the
    receipt commits, so a genuinely full DB loses no receipt and no cut."""

    conn = _trade_db(tmp_path)
    witness = _witness()
    used = conn.execute("PRAGMA page_count").fetchone()[0]
    # Room for the receipt row (overflow pages included), none for the corpus.
    conn.execute(f"PRAGMA max_page_count = {used + 12}")
    row_id = _store(conn, _no_winner_decision(witness), witness=witness, flush=False)
    assert row_id is not None
    # A second queued cut with 60 families needs fresh pages (a small cut can
    # fit in the slack of existing root pages); both share one flush.
    wide = {f"fam-{i}": _witness(family=f"fam-{i}") for i in range(60)}
    wide_cut = gbr._tier0_cut_corpus(
        selection_epoch_identity="epoch-wide", reason="R", decision_at_utc=AT,
        scope_family_count=60, probability_witnesses=wide, ineligible_by_family={},
        excluded_by_family={}, evaluations=(), winner_candidate_id=None,
        book_epoch=None, family_context_by_key={},
        fractional_kelly_multiplier=Decimal("0.25"), buy_candidates_enabled=True,
    )
    key = gbr._decision_log_connection_key(conn)
    corpus.queue_cut(key, corpus.PendingCut(lambda: (wide_cut, ()), None))
    # Commit ever-smaller filler until even one byte no longer fits: every
    # page is then genuinely full (committed filler keeps its pages), so the
    # corpus INSERT hits a real SQLITE_FULL.
    conn.execute("CREATE TABLE pad (x)")
    conn.commit()
    size = 4096
    while size:
        try:
            conn.execute("INSERT INTO pad VALUES (?)", ("p" * size,))
            conn.commit()
        except sqlite3.OperationalError as exc:
            assert "full" in str(exc)
            conn.rollback()
            size //= 2
    # One cut per transaction: the one-family cut may still fit in page slack;
    # the 60-family cut needs fresh pages and hits a real SQLITE_FULL.
    written = _flush(conn)
    assert written <= 1
    assert not conn.in_transaction
    # The receipt is committed and durable on a fresh connection.
    fresh = sqlite3.connect(tmp_path / "trade.db")
    assert fresh.execute(
        "SELECT COUNT(*) FROM decision_log WHERE id = ?", (row_id,)
    ).fetchone()[0] == 1
    assert fresh.execute(
        "SELECT COUNT(*) FROM tier0_auction_cut WHERE decision_log_id IS NULL"
    ).fetchone()[0] == 0
    fresh.close()
    # Space returns: every still-queued cut is written by the next flush.
    assert len(corpus.pending_cuts(key)[0]) == 2 - written
    conn.execute("PRAGMA max_page_count = 1073741823")
    assert _flush(conn) == 2 - written
    assert [row[0] for row in conn.execute(
        "SELECT decision_log_id FROM tier0_auction_cut ORDER BY cut_seq")] == [row_id, None]
    assert _count(conn, "tier0_cut_family") == 61
    assert corpus.pending_cuts(key) == ((), 0)


def test_missing_stale_and_crossed_quotes_are_stored_with_reasons(tmp_path):
    conn = _trade_db(tmp_path)
    witness = _witness()
    # Book captured 60 s before decision against a 30 s max age -> stale.
    stale_at = AT - _dt.timedelta(seconds=60)
    _store(conn, _no_winner_decision(witness), witness=witness,
           book=_book_epoch(CROSSED_BOOK, at=stale_at))
    book = _book_rows(conn)
    # Crossed YES book: both raw quotes kept verbatim, no mid.
    assert (book["b-mid"]["yes_bid"], book["b-mid"]["yes_ask"]) == ("0.60", "0.55")
    assert (book["b-mid"]["yes_mid"], book["b-mid"]["yes_mid_unavailable_reason"]) == (
        None, "YES_QUOTE_CROSSED")
    # Missing YES quote: no price invented, reason stated.
    assert book["b-low"]["yes_ask"] is None
    assert (book["b-low"]["yes_mid"], book["b-low"]["yes_mid_unavailable_reason"]) == (
        None, "YES_BID_MISSING")
    assert book["b-high"]["yes_mid_unavailable_reason"] == "VENUE_NOT_EXECUTABLE"
    assert _only(conn, "tier0_family_snapshot")["market_reference_complete"] == 0
    cut = corpus.decode_payload(_only(conn, "tier0_auction_cut")["payload"])
    assert cut["book_stale"] is True
    assert cut["book_age_seconds"] == pytest.approx(61.0)


def test_non_positive_quote_is_reported_not_patched():
    assert corpus._yes_mid("EXECUTABLE", "0", "0.4") == (None, "YES_QUOTE_NON_POSITIVE")
    assert corpus._yes_mid(None, "0.4", "0.5") == (None, "BOOK_SIDE_NOT_CAPTURED")


def test_unreceipted_cut_is_recorded_by_the_next_flush(tmp_path):
    conn = _trade_db(tmp_path)
    key = gbr._decision_log_connection_key(conn)
    for reason in (
        "GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED",
        "GLOBAL_AUCTION_NO_CURRENT_PROBABILITY_FAMILY",
    ):
        gbr._queue_unreceipted_tier0_cut(
            conn, reason=reason, decision_at_utc=AT, economic_cut_completed=False,
            event_count=1, fractional_kelly_multiplier=Decimal("0.25"),
            buy_candidates_enabled=True,
        )
    assert len(corpus.pending_cuts(key)[0]) == 2
    witness = _witness()
    _store(conn, _no_winner_decision(witness), witness=witness)
    statuses = sorted(
        (row["status"], row["reason"])
        for row in conn.execute("SELECT status, reason FROM tier0_auction_cut")
    )
    assert statuses == [
        ("INCOMPLETE", "GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED"),
        ("NO_CANDIDATES", "GLOBAL_AUCTION_NO_CURRENT_PROBABILITY_FAMILY"),
        ("NO_TRADE", "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER"),
    ]
    assert corpus.pending_cuts(key) == ((), 0)


def test_backlog_drains_in_bounded_flushes_and_overflow_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(corpus, "_FLUSH_LIMIT", 2)
    monkeypatch.setattr(corpus, "_QUEUE_LIMIT", 4)
    conn = _trade_db(tmp_path)
    key = gbr._decision_log_connection_key(conn)
    for index in range(6):
        gbr._queue_unreceipted_tier0_cut(
            conn, reason=f"DEFERRED_PREEMPTED:{index}", decision_at_utc=AT,
            economic_cut_completed=False, event_count=1,
            fractional_kelly_multiplier=Decimal("0.25"), buy_candidates_enabled=True,
        )
    assert corpus.pending_cuts(key)[1] == 2  # two oldest dropped, counted
    assert _flush(conn) == 2
    reasons = [row[0] for row in conn.execute(
        "SELECT reason FROM tier0_auction_cut ORDER BY cut_seq")]
    assert reasons == ["DEFERRED_PREEMPTED:2", "DEFERRED_PREEMPTED:3",
                       "CORPUS_QUEUE_OVERFLOW:2"]
    assert len(corpus._PENDING[key]) == 2 and corpus.pending_cuts(key)[1] == 0


def _label_fixture(conn):
    topology_row = _only(conn, "tier0_family_topology")
    forecast = sqlite3.connect(":memory:")
    forecast.row_factory = sqlite3.Row
    forecast.executescript(
        """
        CREATE TABLE market_events (condition_id TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, range_low REAL, range_high REAL);
        CREATE TABLE settlement_outcomes (city TEXT, target_date TEXT,
            temperature_metric TEXT, settlement_value REAL, settlement_unit TEXT,
            authority TEXT, settled_at TEXT, recorded_at TEXT);
        """
    )
    forecast.executemany(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        [(f"cond-{b}", "London", "2026-09-26", "high", lo, hi) for b, lo, hi in BINS],
    )
    forecast.execute(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?,?,?)",
        ("London", "2026-09-26", "high", 21.0, "C", "VERIFIED",
         "2026-09-27T01:00:00+00:00", "2026-09-27T01:30:00+00:00"),
    )
    topology = {
        "topology_seq": int(topology_row["topology_seq"]),
        "city": "London", "target_date": "2026-09-26",
        "bindings": corpus.decode_payload(topology_row["payload"])["bindings"],
    }
    return forecast, topology


def test_family_fold_labels_in_settlement_order_with_availability(tmp_path):
    conn = _trade_db(tmp_path)
    witness = _witness()
    _store(conn, _no_winner_decision(witness), witness=witness)
    forecast, topology = _label_fixture(conn)
    labels, stats = ptc._tier0_family_labels(forecast, [topology])
    assert stats["family_labels_ready"] == 1
    label = labels[0]
    payload = corpus.decode_payload(label[6])
    # Witness order is (mid, low, high); settlement order is (low, mid, high).
    assert payload["settlement_order"] == [1, 0, 2]
    assert label[3] == 2 and payload["yes_won"] == [0, 0, 1]
    assert label[7] == "2026-09-27T01:30:00+00:00"


def _insert_label(conn, topology_seq, available_at):
    conn.execute(
        "INSERT INTO tier0_family_label VALUES (?,?,?,?,?,?,?,?,?)",
        (topology_seq, 21.0, "C", 2, "enc", "sha", b"x", available_at, available_at),
    )


def _retain(conn, now):
    cutoff = (now - _dt.timedelta(days=ptc._TIER0_CORPUS_RETENTION_DAYS)).isoformat()
    cut_cutoff = (now - _dt.timedelta(days=ptc._TIER0_CUT_RETENTION_DAYS)).isoformat()
    total = {"links": 0, "states": 0, "topologies": 0, "cuts": 0}
    while True:
        step = ptc._tier0_corpus_retention_step(
            conn, cutoff_iso=cutoff, cut_cutoff_iso=cut_cutoff, limit=1000
        )
        conn.commit()
        for key, value in step.items():
            total[key] += value
        if not any(step.values()):
            return total


def _two_families(conn):
    """Cut 1 at AT: families A and B. Cut 2 at AT+1d: family B only."""

    wa, wb = _witness(family="fam-a"), _witness(family="fam-b")
    later = AT + _dt.timedelta(days=1)
    for epoch, at, witnesses in (
        ("e-1", AT, {"fam-a": wa, "fam-b": wb}),
        ("e-2", later, {"fam-b": _witness(family="fam-b", at=later)}),
    ):
        built = gbr._tier0_cut_corpus(
            selection_epoch_identity=epoch, reason="R", decision_at_utc=at,
            scope_family_count=len(witnesses), probability_witnesses=witnesses,
            ineligible_by_family={}, excluded_by_family={}, evaluations=(),
            winner_candidate_id=None, book_epoch=None, family_context_by_key={},
            fractional_kelly_multiplier=Decimal("0.25"), buy_candidates_enabled=True,
        )
        corpus.write_cut(conn, built, decision_log_id=None)
    conn.commit()
    return {
        row["family_key"]: row["topology_seq"]
        for row in conn.execute("SELECT family_key, topology_seq FROM tier0_family_topology")
    }


def test_retention_never_deletes_an_unlabelled_family(tmp_path):
    conn = _trade_db(tmp_path)
    seqs = _two_families(conn)
    _insert_label(conn, seqs["fam-a"], "2026-09-27T00:00:00+00:00")  # labelled, old
    conn.commit()
    deleted = _retain(conn, now=AT + _dt.timedelta(days=90))
    assert deleted["links"] == 1 and deleted["topologies"] == 1
    # Family B has no label: both its links, its state and its topology stay,
    # and so do both cuts that reach it.
    assert _count(conn, "tier0_cut_family") == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM tier0_cut_family WHERE topology_seq = ?", (seqs["fam-b"],)
    ).fetchone()[0] == 2
    assert _count(conn, "tier0_auction_cut") == 2
    assert [row[0] for row in conn.execute("SELECT family_key FROM tier0_family_topology")] == ["fam-b"]
    assert _count(conn, "tier0_family_label") == 1  # labels are kept


def test_retention_keeps_labelled_rows_inside_the_validation_window(tmp_path):
    conn = _trade_db(tmp_path)
    seqs = _two_families(conn)
    for seq in seqs.values():
        _insert_label(conn, seq, "2026-09-27T00:00:00+00:00")
    conn.commit()
    # 29 days after availability: nothing may go.
    assert not any(_retain(conn, now=_dt.datetime(2026, 10, 26, tzinfo=_dt.timezone.utc)).values())
    assert _count(conn, "tier0_cut_family") == 3
    # 31 days after: every family row goes; the now family-less cut rows are
    # 32-33 days old and stay until their own 60-day cutoff.
    deleted = _retain(conn, now=_dt.datetime(2026, 10, 28, tzinfo=_dt.timezone.utc))
    assert deleted == {"links": 3, "states": 2, "topologies": 2, "cuts": 0}
    for table in ("tier0_cut_family", "tier0_family_snapshot", "tier0_family_topology"):
        assert _count(conn, table) == 0
    assert _count(conn, "tier0_auction_cut") == 2
    assert _retain(conn, now=_dt.datetime(2026, 11, 27, tzinfo=_dt.timezone.utc))["cuts"] == 2
    assert _count(conn, "tier0_auction_cut") == 0


def test_retention_never_deletes_a_cut_a_family_still_reaches(tmp_path):
    conn = _trade_db(tmp_path)
    _two_families(conn)  # no labels at all
    deleted = _retain(conn, now=AT + _dt.timedelta(days=365))
    assert not any(deleted.values())
    assert _count(conn, "tier0_auction_cut") == 2 and _count(conn, "tier0_cut_family") == 3


def test_retention_is_bounded_per_chunk(tmp_path):
    conn = _trade_db(tmp_path)
    seqs = _two_families(conn)
    for seq in seqs.values():
        _insert_label(conn, seq, "2026-09-27T00:00:00+00:00")
    conn.commit()
    step = ptc._tier0_corpus_retention_step(
        conn, cutoff_iso="2026-10-28T00:00:00+00:00",
        cut_cutoff_iso="2026-12-01T00:00:00+00:00", limit=1,
    )
    assert step["links"] == 1 and step["cuts"] == 0


def test_retention_run_pauses_on_a_large_wal(tmp_path, monkeypatch):
    monkeypatch.setattr(ptc, "_wal_bytes", lambda _path: ptc._TIER0_CORPUS_WAL_BYTES_LIMIT + 1)
    monkeypatch.setattr(ptc, "_coordinated_trade_writes",
                        lambda *a, **k: lambda: pytest.fail("must not open a write"))
    stats = ptc.run_tier0_corpus_retention(now=AT)
    assert stats["wal_paused"] == 1 and stats["chunks"] == 0


def test_growth_report_counts_rows_and_bytes_per_table(tmp_path):
    conn = _trade_db(tmp_path)
    witness = _witness()
    _store(conn, _no_winner_decision(witness), witness=witness)
    growth = ptc.tier0_corpus_growth(conn, since_iso="2000-01-01T00:00:00+00:00")
    assert growth["tier0_auction_cut"]["new_rows"] == 1
    assert growth["tier0_family_snapshot"]["payload_bytes"] == len(
        _only(conn, "tier0_family_snapshot")["payload"])
    assert growth["tier0_cut_family"] == {"rows": 1, "new_rows": 1}
    assert ptc.tier0_corpus_growth(
        conn, since_iso="2999-01-01T00:00:00+00:00"
    )["tier0_auction_cut"]["new_rows"] == 0


def test_corpus_adds_no_work_to_the_receipt_stage(tmp_path, monkeypatch):
    """The receipt only queues a builder; the corpus is built and written by
    the post-batch flush, so neither can delay a pre-submit receipt."""

    calls = []
    monkeypatch.setattr(gbr, "_tier0_cut_corpus", lambda **k: calls.append(1) or pytest.fail("built on the receipt path"))
    conn = _trade_db(tmp_path)
    witness = _witness()
    assert _store(conn, _no_winner_decision(witness), witness=witness, flush=False)
    assert calls == [] and _count(conn, "tier0_auction_cut") == 0


def test_receipt_corpus_budget_on_a_live_sized_cut(tmp_path):
    """Post-commit build+write per cut on a live-median-sized cut (220 families x
    11 bins, ~4,100 legs). The gate is loose (CI noise); the value is reported."""

    evaluations, witnesses, states, assets = [], {}, [], []
    for f in range(220):
        family = f"fam-{f}"
        bindings = tuple(
            OutcomeTokenBinding(bin_id=f"{family}-b{k}", condition_id=f"c-{family}-{k}",
                                yes_token_id=f"y-{family}-{k}", no_token_id=f"n-{family}-{k}")
            for k in range(11)
        )
        point = np.full(11, 1.0 / 11.0)
        fields = dict(
            family_key=family, bindings=bindings, q_version="q", resolution_identity="r",
            topology_identity="t", posterior_identity_hash="p", source_truth_identity="s",
            authority_certificate_hash="a", band_alpha=0.05, band_basis="b",
            yes_point_q=point, yes_q_samples=np.tile(point, (400, 1)), captured_at_utc=AT,
        )
        w = JointOutcomeProbabilityWitness(
            **fields, max_age=_dt.timedelta(minutes=3),
            witness_identity=joint_probability_witness_identity(**fields),
        )
        witnesses[family] = w
        for binding in bindings:
            for side, token in (("YES", binding.yes_token_id), ("NO", binding.no_token_id)):
                states.append((family, binding.bin_id, binding.condition_id, side, token,
                               "EXECUTABLE", f"h-{token}", "e", "g", "False"))
                assets.append(CurrentGlobalBookAsset(
                    family_key=family, bin_id=binding.bin_id, condition_id=binding.condition_id,
                    gamma_market_id="g", market_event_id="e", side=side, token_id=token,
                    curve=_curve(token, side, "0.55"), captured_at_utc=AT, neg_risk=False,
                    bid_levels=(BidBookLevel(price=Decimal("0.45"), size=Decimal("10")),),
                ))
                if len(evaluations) < 4100:
                    evaluations.append(GlobalSingleOrderCandidateEvaluation(
                        candidate_id=f"{side}:{binding.bin_id}", family_key=family,
                        bin_id=binding.bin_id, condition_id=binding.condition_id, side=side,
                        token_id=token, action="BUY", status="REJECTED",
                        rejection_reason="LIVE_UNIT_PRICE_OUT_OF_BOUNDS",
                        probability_witness_identity=w.witness_identity,
                    ))
    book = CurrentGlobalBookEpoch(
        assets=tuple(assets), asset_states=tuple(states), captured_at_utc=AT,
        max_age=_dt.timedelta(seconds=30),
        witness_identity=current_global_book_epoch_identity(asset_states=tuple(states), captured_at_utc=AT),
    )
    conn = _trade_db(tmp_path)
    key = gbr._decision_log_connection_key(conn)
    flush_ms = []
    for i in range(5):
        cut_at = AT + _dt.timedelta(seconds=i)

        def build(i=i, cut_at=cut_at):
            return gbr._tier0_cut_corpus(
                selection_epoch_identity=f"e-{i}", reason="R", decision_at_utc=cut_at,
                scope_family_count=220, probability_witnesses=witnesses, ineligible_by_family={},
                excluded_by_family={}, evaluations=evaluations, winner_candidate_id=None,
                book_epoch=book, family_context_by_key={},
                fractional_kelly_multiplier=Decimal("0.25"), buy_candidates_enabled=True,
            ), ()

        corpus.queue_cut(key, corpus.PendingCut(build, i))
        started = time.perf_counter()
        assert _flush(conn) == 1
        flush_ms.append((time.perf_counter() - started) * 1000)
    print(f"TIER0_CORPUS_FLUSH_MS {sorted(flush_ms)}")
    assert _count(conn, "tier0_family_snapshot") == 220  # 5 identical cuts dedupe
    assert _count(conn, "tier0_cut_family") == 1100
    assert sorted(flush_ms)[2] < 1500


def test_live_call_sites_are_wired():
    """Exists-but-unwired antibody for the three live seams: the receipt call
    passes the frozen witness/book, reject() queues an unreceipted cut, and the
    batch's finally flushes after every receipt and venue call."""

    import ast
    import inspect

    tree = ast.parse(inspect.getsource(gbr.process_current_global_batch))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_store_global_auction_receipt"
    ]
    assert len(calls) == 1
    keywords = {kw.arg: ast.unparse(kw.value) for kw in calls[0].keywords}
    assert keywords["probability_witnesses"] == "attempt_probabilities"
    assert keywords["book_epoch"] == "attempt_book_epoch"
    assert keywords["buy_candidates_enabled"] == "buy_candidates_enabled"
    reject = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "reject"
    )
    assert "_queue_unreceipted_tier0_cut" in ast.unparse(reject)
    assert "cut_receipt_written = True" in ast.unparse(tree)
    outer = next(node for node in tree.body[0].body if isinstance(node, ast.Try))
    assert "_flush_tier0_learning_corpus" in ast.unparse(outer.finalbody)


def test_flush_yields_the_last_free_disk_space_and_keeps_the_cut(tmp_path, monkeypatch):
    conn = _trade_db(tmp_path)
    witness = _witness()
    monkeypatch.setattr(gbr, "_TIER0_CORPUS_MIN_FREE_BYTES", 1 << 62)
    row_id = _store(conn, _no_winner_decision(witness), witness=witness)
    assert row_id is not None and _count(conn, "tier0_auction_cut") == 0
    key = gbr._decision_log_connection_key(conn)
    assert len(corpus.pending_cuts(key)[0]) == 1
    monkeypatch.setattr(gbr, "_TIER0_CORPUS_MIN_FREE_BYTES", 0)
    assert _flush(conn) == 1 and _count(conn, "tier0_auction_cut") == 1


def test_retention_run_deletes_nothing_inside_the_window(tmp_path, monkeypatch):
    """The scheduled entry point (not just the step) honours the 30-day window."""

    conn = _trade_db(tmp_path)
    seqs = _two_families(conn)
    for seq in seqs.values():
        _insert_label(conn, seq, "2026-09-27T00:00:00+00:00")
    conn.commit()
    path = tmp_path / "trade.db"

    def transaction():
        from contextlib import contextmanager

        @contextmanager
        def tx():
            c = sqlite3.connect(path)
            try:
                yield SimpleNamespace(connection=c)
                c.commit()
            finally:
                c.close()
        return tx()

    monkeypatch.setattr(ptc, "_coordinated_trade_writes", lambda *a, **k: transaction)
    monkeypatch.setattr(ptc, "_wal_bytes", lambda _path: 0)
    stats = ptc.run_tier0_corpus_retention(now=_dt.datetime(2026, 10, 20, tzinfo=_dt.timezone.utc))
    assert stats["links"] == stats["states"] == stats["topologies"] == stats["cuts"] == 0
    assert _count(conn, "tier0_cut_family") == 3
    stats = ptc.run_tier0_corpus_retention(now=_dt.datetime(2026, 10, 28, tzinfo=_dt.timezone.utc))
    assert stats["links"] == 3 and _count(conn, "tier0_cut_family") == 0


def test_flush_writes_one_cut_per_transaction(tmp_path, monkeypatch):
    """Lock hold is bounded by construction: every transaction carries exactly
    one cut, however many are queued."""

    conn = _trade_db(tmp_path)
    key = gbr._decision_log_connection_key(conn)
    for index in range(5):
        gbr._queue_unreceipted_tier0_cut(
            conn, reason=f"DEFERRED_PREEMPTED:{index}", decision_at_utc=AT,
            economic_cut_completed=False, event_count=1,
            fractional_kelly_multiplier=Decimal("0.25"), buy_candidates_enabled=True,
        )
    rows_per_transaction = []
    real = gbr._tier0_corpus_transaction

    def counting(conn_, work_context, write):
        before = _count(conn_, "tier0_auction_cut")
        real(conn_, work_context, write)
        rows_per_transaction.append(_count(conn_, "tier0_auction_cut") - before)

    monkeypatch.setattr(gbr, "_tier0_corpus_transaction", counting)
    assert _flush(conn) == 5
    assert rows_per_transaction == [1, 1, 1, 1, 1]
    assert corpus.pending_cuts(key) == ((), 0)


def test_flush_stops_at_its_wall_time_budget_and_keeps_the_rest(tmp_path, monkeypatch):
    conn = _trade_db(tmp_path)
    key = gbr._decision_log_connection_key(conn)
    for index in range(4):
        gbr._queue_unreceipted_tier0_cut(
            conn, reason=f"R{index}", decision_at_utc=AT, economic_cut_completed=False,
            event_count=1, fractional_kelly_multiplier=Decimal("0.25"),
            buy_candidates_enabled=True,
        )
    clock = iter([0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    monkeypatch.setattr(gbr.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(gbr, "_TIER0_CORPUS_FLUSH_BUDGET_S", 0.25)
    assert _flush(conn) == 3
    assert len(corpus.pending_cuts(key)[0]) == 1


def test_retention_chunk_deletes_at_most_its_limit_per_table(tmp_path):
    conn = _trade_db(tmp_path)
    seqs = _two_families(conn)
    for seq in seqs.values():
        _insert_label(conn, seq, "2026-09-27T00:00:00+00:00")
    conn.commit()
    step = ptc._tier0_corpus_retention_step(
        conn, cutoff_iso="2026-10-28T00:00:00+00:00",
        cut_cutoff_iso="2026-12-31T00:00:00+00:00", limit=2,
    )
    assert all(value <= 2 for value in step.values())
    assert step["links"] == 2


def test_retention_cut_probe_is_bounded_by_the_oldest_window(tmp_path):
    """A pinned (still-linked) oldest prefix costs a fixed probe; cuts beyond
    the window wait for later chunks instead of forcing a full scan."""

    conn = _trade_db(tmp_path)
    _two_families(conn)  # 2 linked, unlabelled cuts: seq 1 and 2
    for index in range(3):
        corpus.write_cut(conn, corpus.build_unreceipted_cut(
            reason=f"R{index}", decision_at_utc=AT, selection_policy_identity="p",
            economic_cut_completed=False, detail={},
        ), decision_log_id=None)
    conn.commit()
    step = ptc._tier0_corpus_retention_step(
        conn, cutoff_iso="2000-01-01", cut_cutoff_iso="2026-12-31T00:00:00+00:00", limit=2,
    )
    assert step["cuts"] == 0  # window = the two linked cuts
    step = ptc._tier0_corpus_retention_step(
        conn, cutoff_iso="2000-01-01", cut_cutoff_iso="2026-12-31T00:00:00+00:00", limit=5,
    )
    assert step["cuts"] == 3 and _count(conn, "tier0_auction_cut") == 2


def test_batch_flush_failure_never_escapes_the_batch(tmp_path, monkeypatch):
    """Review round 2: the closing flush sits in the batch's finally; if it
    raised, the batch would not return (or would mask its own exception)."""

    conn = _trade_db(tmp_path)
    monkeypatch.setattr(gbr, "_flush_tier0_learning_corpus",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("flush boom")))
    gbr._flush_tier0_learning_corpus_after_batch(conn)  # must not raise
    monkeypatch.setattr(gbr, "_flush_tier0_learning_corpus",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        gbr._flush_tier0_learning_corpus_after_batch(conn)


def test_batch_flush_contains_a_non_exception_base_exception(tmp_path, monkeypatch):
    """Any BaseException short of process shutdown is contained, not just
    Exception subclasses (e.g. a GeneratorExit or a custom BaseException)."""

    class Odd(BaseException):
        pass

    conn = _trade_db(tmp_path)
    monkeypatch.setattr(gbr, "_flush_tier0_learning_corpus",
                        lambda *a, **k: (_ for _ in ()).throw(Odd("odd")))
    gbr._flush_tier0_learning_corpus_after_batch(conn)  # must not raise



def test_wide_cut_splits_into_bounded_content_transactions(tmp_path, monkeypatch):
    """Commit latency tracks bytes committed, so a 60-family cut is written as
    content chunks of at most _TIER0_CORPUS_ROWS_PER_TRANSACTION new rows, then
    one publishing transaction; the cut is invisible until that last one."""

    monkeypatch.setattr(gbr, "_TIER0_CORPUS_ROWS_PER_TRANSACTION", 16)
    conn = _trade_db(tmp_path)
    key = gbr._decision_log_connection_key(conn)
    wide = {f"fam-{i}": _witness(family=f"fam-{i}") for i in range(60)}
    built = gbr._tier0_cut_corpus(
        selection_epoch_identity="epoch-wide", reason="R", decision_at_utc=AT,
        scope_family_count=60, probability_witnesses=wide, ineligible_by_family={},
        excluded_by_family={}, evaluations=(), winner_candidate_id=None,
        book_epoch=None, family_context_by_key={},
        fractional_kelly_multiplier=Decimal("0.25"), buy_candidates_enabled=True,
    )
    corpus.queue_cut(key, corpus.PendingCut(lambda: (built, ()), None))
    per_tx = []
    real = gbr._tier0_corpus_transaction
    tables = ("tier0_family_snapshot", "tier0_family_topology", "tier0_auction_cut")

    def counting(conn_, work_context, write):
        before = [_count(conn_, t) for t in tables]
        real(conn_, work_context, write)
        per_tx.append(tuple(_count(conn_, t) - b for t, b in zip(tables, before)))

    monkeypatch.setattr(gbr, "_tier0_corpus_transaction", counting)
    assert _flush(conn) == 1
    assert len(per_tx) == 5  # ceil(60 / 16) content chunks + 1 publish
    assert all(states <= 16 and topologies <= 16 for states, topologies, _ in per_tx)
    assert [cuts for _, _, cuts in per_tx] == [0, 0, 0, 0, 1]
    assert _count(conn, "tier0_cut_family") == 60


def test_corpus_transaction_disables_autocheckpoint_and_restores_it(tmp_path):
    """No inline WAL checkpoint may run while the corpus holds the write lock
    (measured up to ~390 ms); the connection's own setting is restored."""

    conn = _trade_db(tmp_path)
    conn.execute("PRAGMA wal_autocheckpoint=777")
    seen = []
    gbr._tier0_corpus_transaction(
        conn, None,
        lambda: seen.append(conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]),
    )
    assert seen == [0]
    assert conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 777
