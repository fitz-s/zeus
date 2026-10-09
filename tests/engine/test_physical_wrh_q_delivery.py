# Created: 2026-10-06
# Last reused/audited: 2026-10-07
# Authority basis: offline native WRH current-product / lawful exit investigation.
"""Native WRH physical revision to current q and lawful global selection.

Synthetic provider bodies use actual typed source writers/readers. Forecast
inputs reuse the owned station/ground/provider fixture with controlled fusion
output; independent v6 model serving is covered by the normal HKO acceptance
control. This is not a deployed acquisition or historical liquidity replay. The final
case composes the actual batch winner with JIT/lifecycle/executor and a confirmed
fake-venue fill. Queue drainage and batch-to-executor scheduling are explicit
fixture boundaries; current WRH custody, probability admission and selection
authority remain real.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from zoneinfo import ZoneInfo

import pytest
from tests import test_replacement_forecast_materializer as materializer_harness
from tests.test_replacement_forecast_materializer import (  # noqa: F401: pytest fixtures
    _hko_source_surface, _hko_native_surfaces,
)


def _native_current_product(city, *, target, received, values, metadata=None):
    from src.data import noaa_wrh_timeseries as wrh
    start = datetime.combine(target, datetime.min.time(), ZoneInfo(city.timezone)).astimezone(timezone.utc)
    points = [(start + timedelta(minutes=30 + index * 30),value) for index,value in enumerate(values) if value is not None]
    instants = [point[0] for point in points]
    body = json.dumps({"SUMMARY":{"RESPONSE_CODE":1},"UNITS":{"air_temp":"Celsius"},
        "STATION":[{"STID":city.wu_station,"OBSERVATIONS":{
            "date_time":[at.astimezone(ZoneInfo(city.timezone)).strftime("%Y-%m-%dT%H:%M:%S%z") for at in instants],
            "air_temp_set_1":[point[1] for point in points],"sea_level_pressure_set_1":[1010]*len(points)}}]},sort_keys=True).encode()
    if metadata is not None:
        payload=json.loads(body);payload["transport_note"]=metadata;body=json.dumps(payload,sort_keys=True).encode()
    product = wrh.product_from_response(body,city.wu_station,unit=city.settlement_unit,
        fetched_at=received,source_response_sha256=hashlib.sha256(body).hexdigest())
    return replace(product,request_started_at=received-timedelta(seconds=1),
        coverage_start_utc=start-timedelta(hours=1),coverage_end_utc=received-timedelta(seconds=1))


def _initialize_source_owners(conn, tmp_path, monkeypatch):
    from src.state import db, write_coordinator
    monkeypatch.setattr(write_coordinator,"_DEFAULT_RUNTIME_COORDINATOR",None)
    world = tmp_path / "zeus-world.db"
    monkeypatch.setattr(db,"ZEUS_WORLD_DB_PATH",world)
    forecast_path = next(r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main")
    from pathlib import Path
    monkeypatch.setattr(db,"ZEUS_FORECASTS_DB_PATH",Path(forecast_path))
    with sqlite3.connect(world) as world_conn:
        db.init_schema_world_only(world_conn)
    conn.commit()
    db.init_schema_forecasts(conn)
    conn.commit()
    conn.execute("ATTACH DATABASE ? AS world", (str(world),))
    assert "world" in {r[1] for r in conn.execute("PRAGMA database_list")}


def _write_product(conn, city, request, *, at, values, metadata=None):
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    product = _native_current_product(city,target=request.target_date,received=at,values=values,metadata=metadata)
    conn.commit()
    status = append_current_noaa_wrh_product(conn,city=city,target_date=str(request.target_date),product=product,as_of=at)
    conn.commit()
    owned, snapshot = read_current_noaa_wrh_snapshot(conn,city=city,target_date=str(request.target_date),as_of=at)
    assert owned and snapshot is not None
    if status != "noop":
        assert snapshot.response_sha256 == product.response_sha256
    assert [row.air_temp for row in snapshot.rows] == [value for value in values if value is not None]
    return snapshot, status


@pytest.fixture
def wrh_case(tmp_path,monkeypatch,_hko_source_surface):
    from src.config import runtime_cities_by_name
    conn, request = materializer_harness._shanghai_current_owner_request(
        tmp_path,monkeypatch,observed_extreme=26.0,record_observed_prints=False)
    _initialize_source_owners(conn,tmp_path,monkeypatch)
    city = runtime_cities_by_name()[request.city]
    request = replace(request,bins=(
        materializer_harness._TemperatureBin("29°C or below",upper_c=29.0,center_c=28.0),
        materializer_harness._TemperatureBin("30°C",lower_c=30.0,upper_c=30.0,center_c=30.0),
        materializer_harness._TemperatureBin("31°C or higher",lower_c=31.0,center_c=32.0)))
    trade, position = _market_and_holding(conn,request,tmp_path,monkeypatch)
    from src.data import replacement_forecast_bundle_reader as bundle_reader
    clock = [request.computed_at]
    class ClockType(type):
        def __instancecheck__(cls,value): return isinstance(value,datetime)
    class DecisionClock(datetime,metaclass=ClockType):
        @classmethod
        def now(cls,tz=None): return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    monkeypatch.setattr(bundle_reader,"datetime",DecisionClock)
    _remaining_vectors(conn,city,request)
    case=SimpleNamespace(conn=conn,request=request,city=city,trade=trade,position=position,
        clock=clock,monkeypatch=monkeypatch)
    try:
        yield case
    finally:
        trade.close()
        conn.close()


def _advance(case,values,*,minute=0,metadata=None):
    from src.data import day0_hourly_vectors as hourly
    from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    from src.engine import monitor_refresh
    conn,request,city=case.conn,case.request,case.city
    at=request.computed_at+timedelta(minutes=minute)
    case.clock[0]=at
    product,status=_write_product(conn,city,request,at=at,values=values,metadata=metadata)
    state=hourly.read_day0_current_temperature_state(conn=conn,city=city,target_date=str(request.target_date),decision_time=at)
    fact=_latest_authorized_day0_fact(conn,city=city.name,target_date=str(request.target_date),
        temperature_metric=request.temperature_metric,decision_time=at,require_settlement_channel=True)
    assert fact is not None and fact["unit"]=="C"
    # Composition boundary: seed fields come from the current canonical owner,
    # never a hand-picked q or stale scalar. Automatic queue drain is separate.
    current=materializer_harness._refresh_shanghai_owner_request(conn,case.monkeypatch,
        replace(request,computed_at=at,day0_observed_extreme_c=fact["observed_extreme_native"],
            day0_observed_extreme_source=fact["observation_source"],
            day0_observed_extreme_observation_time=fact["observation_time"],
            day0_observed_extreme_sample_count=fact["sample_count"],day0_observed_extreme_unit=fact["unit"]),
        record_observed_prints=False)
    result=materialize_replacement_forecast_live(conn,current)
    assert result.ok,result.reason_codes
    conn.commit()  # Independent current-q readers must observe a durable write.
    row=conn.execute("SELECT q_json,provenance_json FROM forecast_posteriors WHERE posterior_id=?",(result.posterior_id,)).fetchone()
    snapshot=monitor_refresh._build_current_global_day0_family_snapshot(case.position,
        trade_conn=case.trade,decision_time=at,cached_snapshots=())
    held_q,refreshed,_=monitor_refresh._materialize_current_global_day0_probability(case.position,snapshot)
    assert 0 < held_q < 1
    selection=_select_current_wrh(snapshot=snapshot,position=case.position,at=at,monkeypatch=case.monkeypatch)
    return SimpleNamespace(at=at,product=product,status=status,state=state,fact=fact,request=current,
        result=result,q=json.loads(row["q_json"]),provenance=json.loads(row["provenance_json"]),
        snapshot=snapshot,held_q=held_q,refreshed=refreshed,selection=selection)


@pytest.mark.parametrize("restored_values",((26.,25.),(None,25.)),ids=("correction","peak_deletion"))
def test_native_wrh_revision_reprices_positive_q_and_global_sale(wrh_case,restored_values,record_property):
    before=_advance(wrh_case,(26.,25.))
    deteriorated=_advance(wrh_case,(30.,25.),minute=1)
    restored=_advance(wrh_case,restored_values,minute=2)
    assert before.state.value_native==deteriorated.state.value_native==restored.state.value_native==25.
    assert before.state.observed_at==deteriorated.state.observed_at==restored.state.observed_at
    assert before.state.identity()!=deteriorated.state.identity()!=restored.state.identity()
    assert before.selection.candidate is None
    selected=deteriorated.selection
    assert selected.candidate is not None and selected.candidate.action=="SELL"
    assert selected.candidate.execution_mode=="TAKER_LIMIT"
    assert selected.candidate.probability_functional=="POSTERIOR_PREDICTIVE_MEAN"
    assert 0 < deteriorated.held_q < before.held_q < 1
    assert selected.expected_terminal_wealth.expected_ev_usd > 0
    assert selected.expected_terminal_wealth.expected_delta_log_wealth > 0
    assert D(".05") <= selected.candidate.executable_sell_curve.levels[0].price <= D(".95")
    assert restored.held_q > deteriorated.held_q and restored.selection.candidate is None
    if restored_values==(26.,25.):
        assert restored.state.identity()==before.state.identity()
        assert restored.held_q==pytest.approx(before.held_q)
    record_property("native_wrh_current_q",json.dumps({"before":before.held_q,"deteriorated":deteriorated.held_q,
        "restored":restored.held_q,"bid":".07","fee_rate":".05","shares":str(selected.shares)}))


def test_nonwinning_native_revision_changes_identity_without_forced_sale(wrh_case):
    before=_advance(wrh_case,(26.,24.,25.))
    changed=_advance(wrh_case,(26.,23.,25.),minute=1)
    assert before.state.identity()!=changed.state.identity()
    assert before.state.value_native==changed.state.value_native
    assert before.fact["observed_extreme_native"]==changed.fact["observed_extreme_native"]==26.
    assert before.held_q==pytest.approx(changed.held_q)
    assert before.selection.candidate is None and changed.selection.candidate is None


def test_metadata_only_current_confirmation_does_not_renew_economic_identity(wrh_case):
    before=_advance(wrh_case,(26.,25.))
    again=_advance(wrh_case,(26.,25.),minute=1,metadata="new transport-only metadata")
    assert again.status=="noop"
    assert again.product.received_at==before.product.received_at
    assert again.state.identity()==before.state.identity()
    assert again.held_q==pytest.approx(before.held_q)
    assert again.selection.candidate is None


def test_empty_current_product_blocks_old_posterior_and_snapshot_reuse(wrh_case):
    from src.contracts.exceptions import ObservationUnavailableError
    from src.data import day0_hourly_vectors as hourly
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    from src.engine import monitor_refresh
    old=_advance(wrh_case,(30.,25.))
    at=old.at+timedelta(minutes=1)
    product,_=_write_product(wrh_case.conn,wrh_case.city,wrh_case.request,at=at,values=())
    assert product.rows==() and product.extreme("high") is None
    with pytest.raises(ObservationUnavailableError,match="WRH_CURRENT_SNAPSHOT_UNAVAILABLE"):
        hourly.read_day0_current_temperature_state(conn=wrh_case.conn,city=wrh_case.city,
            target_date=str(wrh_case.request.target_date),decision_time=at)
    with pytest.raises(ObservationUnavailableError,match="WRH_CURRENT_SNAPSHOT_UNAVAILABLE"):
        _latest_authorized_day0_fact(wrh_case.conn,city=wrh_case.city.name,target_date=str(wrh_case.request.target_date),
            temperature_metric="high",decision_time=at,require_settlement_channel=True)
    with pytest.raises(ObservationUnavailableError,match="WRH_CURRENT_SNAPSHOT_UNAVAILABLE"):
        monitor_refresh._build_current_global_day0_family_snapshot(wrh_case.position,trade_conn=wrh_case.trade,
            decision_time=at,cached_snapshots=(old.snapshot,))
    assert wrh_case.conn.execute("SELECT 1 FROM forecast_posteriors WHERE posterior_id=?",(old.result.posterior_id,)).fetchone()


@pytest.mark.parametrize("cash,expected",(("10","SELL"),("100","BUY")))
def test_current_native_q_compares_other_proposal_under_current_kelly_law(wrh_case,cash,expected):
    current=_advance(wrh_case,(30.,25.))
    selection=_select_current_wrh(snapshot=current.snapshot,position=wrh_case.position,at=current.at,
        monkeypatch=wrh_case.monkeypatch,competing_buy=True,cash=cash)
    assert selection.candidate is not None and getattr(selection.candidate,"action","BUY")==expected
    if expected=="SELL":
        assert selection.rejection_reasons["wrh-competing-native-buy"]=="FAMILY_JOINT_FRACTIONAL_BUDGET_EXHAUSTED"
    assert len(selection.candidate_evaluations)>=2


def test_out_of_band_native_bid_cannot_authorize_current_positive_q_sale(wrh_case):
    current=_advance(wrh_case,(30.,25.))
    assert current.held_q < .04
    selection=_select_current_wrh(snapshot=current.snapshot,position=wrh_case.position,at=current.at,
        monkeypatch=wrh_case.monkeypatch,bid=".04")
    assert selection.candidate is None


def _market_and_holding(conn,request,tmp_path,monkeypatch):
    from src.state import db
    from src.state.portfolio import Position
    from src.state.snapshot_repo import get_snapshot, insert_snapshot
    from tests.test_exit_safety import _ensure_snapshot
    trade_path=tmp_path/"zeus_trades.db"
    monkeypatch.setattr(db,"_zeus_trade_db_path",lambda:trade_path)
    trade=sqlite3.connect(trade_path); trade.row_factory=sqlite3.Row
    db.init_schema_trade_only(trade)
    _ensure_snapshot(trade,snapshot_id="prototype",captured_at=request.computed_at,
        freshness_deadline=request.computed_at+timedelta(minutes=10))
    prototype=get_snapshot(trade,"prototype")
    for i,item in enumerate(request.bins):
        condition="0x"+f"{i+1:064x}"; yes=f"wrh-yes-{i}"; no=f"wrh-no-{i}"
        conn.execute("INSERT INTO market_events(market_slug,city,target_date,temperature_metric,condition_id,token_id,range_label,range_low,range_high,created_at,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"wrh-{i}",request.city,str(request.target_date),request.temperature_metric,condition,yes,item.bin_id,item.lower_c,item.upper_c,request.computed_at.isoformat(),request.computed_at.isoformat()))
        insert_snapshot(trade,replace(prototype,snapshot_id=f"wrh-book-{i}",condition_id=condition,
            yes_token_id=yes,no_token_id=no,selected_outcome_token_id=no,outcome_label="NO",
            token_map_raw={"YES":yes,"NO":no}))
    conn.commit();trade.commit()
    position=Position(trade_id="wrh-held",market_id="0x"+f"{2:064x}",condition_id="0x"+f"{2:064x}",
        city=request.city,target_date=str(request.target_date),cluster="Asia",temperature_metric=request.temperature_metric,
        bin_label="30°C",direction="buy_no",token_id="wrh-yes-1",no_token_id="wrh-no-1",shares=5,
        chain_shares=5,chain_state="synced",cost_basis_usd=.6,size_usd=.6,entry_price=.12,state="day0_window",unit="C",env="live",strategy_key="forecast_qkernel_entry",entered_at=(request.computed_at-timedelta(hours=1)).isoformat(),
        chain_avg_price=.12,chain_cost_basis_usd=.6,fill_authority="venue_confirmed_full")
    from src.engine.lifecycle_events import build_entry_canonical_write
    events,projection=build_entry_canonical_write(position,phase_after="day0_window",decision_id="wrh-held-entry",
        source_module="tests.engine.test_physical_wrh_q_delivery")
    projection["phase"]="day0_window"
    db.append_many_and_project(trade,events,projection)
    trade.commit()
    return trade,position


def _remaining_vectors(conn,city,request):
    """Same native vector writers as the owned Shanghai carrier fixture."""
    from src.data import day0_hourly_vectors as hourly
    from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
    captured=request.computed_at-timedelta(minutes=2)
    models=hourly.day0_hourly_models_for_city(city)
    ensemble_models=hourly.day0_source_clock_ensemble_member_models()
    times=[f"{request.target_date}T{hour:02d}:00" for hour in range(24)]
    for model in (*models,"__ensemble"):
        ensemble=model=="__ensemble"
        api="ecmwf_ifs025_ensemble" if ensemble else OPENMETEO_MODEL_IDS.get(model,model)
        payload={"latitude":city.lat,"longitude":city.lon,"timezone":city.timezone,"utc_offset_seconds":28800,
            "hourly_units":{"temperature_2m":"°C"},"hourly":{"time":times,"temperature_2m":[30.]*24}}
        if ensemble:
            for index in range(51):
                key="temperature_2m" if index==0 else f"temperature_2m_member{index:02d}"
                payload["hourly"][key]=[30.+(index-25)*.02]*24
        endpoint=hourly.OPENMETEO_ENSEMBLE_URL if ensemble else "https://single-runs-api.open-meteo.com/v1/forecast"
        params={"latitude":city.lat,"longitude":city.lon,"timezone":city.timezone,"hourly":"temperature_2m",
            "models":hourly.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL if ensemble else api,"run":request.source_cycle_time.isoformat()}
        if ensemble:params["metadata_model"]=hourly.DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL
        request_hash=hourly.build_request_hash(endpoint=endpoint,params=params,
            models=list(ensemble_models) if ensemble else [model],captured_at=captured.isoformat(),payload=payload)
        def metadata_for(member):
            return hourly._day0_provider_run_meta(model=member,
                model_api_id=hourly.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL if ensemble else api,
                run=request.source_cycle_time,available_at=request.source_cycle_time+timedelta(hours=8),
                modified_at=request.source_cycle_time+timedelta(hours=8),
                authority="provider_meta_declared" if ensemble else "run_pinned_single_runs",
                endpoint_mode="ensemble_meta_stamped" if ensemble else "single_runs",
                request_params={**params,"endpoint":endpoint},request_hash=request_hash,
                fetch_started_at=captured,fetch_finished_at=captured)
        vectors=hourly.parse_openmeteo_ensemble_hourly_payload(payload,city=city,captured_at=captured.isoformat(),
            source_meta_by_member={member:metadata_for(member) for member in ensemble_models}) if ensemble else hourly.parse_openmeteo_hourly_payload(
                payload,city=city,models=[model],captured_at=captured.isoformat(),source_run_meta_json=json.dumps(metadata_for(model)))
        assert hourly.persist_day0_hourly_vectors(vectors,target_date=str(request.target_date),conn=conn,
            request_hash=request_hash,endpoint=endpoint,now=request.computed_at)==len(vectors)
    conn.commit()


def _select_current_wrh(*,snapshot,position,at,monkeypatch,bid=".07",fee=".05",competing_buy=False,cash="10"):
    from src.solve import solver as S
    from src.solve.solver import ExecutableSellCurve
    from src.contracts.executable_cost_curve import BidBookLevel,FeeModel
    from src.engine.qkernel_spine_bridge import sell_action_authority_identity
    from tests.solve import test_solver_properties as solver_harness
    from tests.integration.test_w3_solve_seam_g3 import _test_wealth_witness
    witness=snapshot.witness
    binding=next(b for b in witness.bindings if b.condition_id==position.condition_id)
    holding=SimpleNamespace(position_id=position.trade_id,family_key=witness.family_key,
        bin_id=binding.bin_id,side="NO",token_id=binding.no_token_id,shares=D("5"))
    curve=ExecutableSellCurve(token_id=holding.token_id,side="NO",snapshot_id="wrh-current-bid",book_hash="wrh-bid-"+bid,
        levels=(BidBookLevel(price=D(bid),size=D("2")),),fee_model=FeeModel(fee_rate=D(fee)),
        min_tick=D(".01"),min_order_size=D("1"),quote_ttl=timedelta(seconds=30))
    status=str(snapshot.day0_payload.get("_edli_day0_exit_authority_status") or "unavailable")
    reason=str(snapshot.day0_payload.get("_edli_day0_exit_authority_reason") or "day0_extreme_maturity_unavailable:missing")
    candidate=S.global_sell_candidate_from_holding(holding,probability_witness=witness,
        ledger_snapshot_id="wrh-ledger",executable_sell_curve=curve,book_captured_at_utc=at,neg_risk=False,
        probability_functional="POSTERIOR_PREDICTIVE_MEAN",execution_mode="TAKER_LIMIT",
        exit_authority_status=status,exit_authority_reason=reason,
        sell_action_authority_identity=sell_action_authority_identity(family_key=witness.family_key,
            probability_witness_identity=witness.witness_identity,status=status,reason=reason))
    wealth=_test_wealth_witness(ledger_snapshot_id="wrh-ledger",position_set_hash="wrh-held-five-no",
        wealth_floor_usd=D(cash),wealth_ceiling_usd=D(cash)+D("5"),spendable_cash_usd=D(cash),reservations_usd=D("0"),
        collateral_authority="CHAIN",captured_at_utc=at,max_age=timedelta(seconds=30),
        native_holdings_micro=((holding.token_id,5_000_000),),
        native_commitments_micro=((holding.token_id,600_000),))
    from src.engine.global_batch_runtime import _bind_selection_holdings
    from src.engine.global_single_order_auction import _candidate_portfolio_endowment, _family_portfolio_endowment
    from src.engine.qkernel_spine_bridge import PreparedGlobalFamily
    from src.state.portfolio import PortfolioState
    prepared=PreparedGlobalFamily(decision_id="wrh-current",probability_witness=witness,candidate_seeds=())
    holdings=_bind_selection_holdings({"wrh":prepared},portfolio_state=PortfolioState(positions=[position]),
        wealth_witness=wealth)["wrh"].holdings_snapshot
    assert len(holdings.holdings)==1 and holdings.holdings[0].shares==D("5")
    family=_family_portfolio_endowment(probability_witness=witness,holdings_snapshot=holdings,wealth_witness=wealth)
    monkeypatch.setattr(solver_harness,"_DECISION_AT",at)
    candidates=[] if candidate is None else [candidate]
    if competing_buy:
        from src.contracts.executable_cost_curve import ExecutableCostCurve,BookLevel
        buy_curve=ExecutableCostCurve(token_id=binding.yes_token_id,side="YES",snapshot_id="wrh-competing-ask",
            book_hash="wrh-ask-.50",levels=(BookLevel(price=D(".50"),size=D("5")),),
            fee_model=FeeModel(fee_rate=D(".05")),min_tick=D(".01"),min_order_size=D("1"),quote_ttl=timedelta(seconds=30))
        candidates.append(S.GlobalSingleOrderCandidate(candidate_id="wrh-competing-native-buy",family_key=witness.family_key,
            bin_id=binding.bin_id,condition_id=binding.condition_id,side="YES",token_id=binding.yes_token_id,
            probability_witness_identity=witness.witness_identity,book_snapshot_id="wrh-competing-ask",book_captured_at_utc=at,
            execution_curve_identity=S.executable_curve_identity(buy_curve),ledger_snapshot_id="wrh-ledger",
            executable_cost_curve=buy_curve,resolution_identity=witness.resolution_identity,neg_risk=False,
            native_bid_levels=(BookLevel(price=D(".49"),size=D("5")),)))
    return solver_harness._global_select(candidates,witness=wealth,
        probability_witnesses={witness.family_key:witness},cap=cash,fractional_kelly_multiplier=".125",
        candidate_portfolio_endowment_resolver=lambda action:_candidate_portfolio_endowment(action,
            probability_witness=witness,holdings_snapshot=holdings,wealth_witness=wealth),
        family_portfolio_endowment_resolver=lambda _:family)


def _real_batch_handoff(case,current,*,cash="1000"):
    """Real adapter and batch selector; explicit synthetic book/wealth, no SDK."""
    from src.engine import event_reactor_adapter as adapter, global_batch_runtime as runtime
    from src.engine import global_auction_universe as universe, monitor_refresh
    from src.solve import solver as S
    from src.state.portfolio import PortfolioState
    from src.contracts.executable_cost_curve import BidBookLevel,FeeModel
    from tests.integration import test_w3_solve_seam_g3 as source_harness
    at=current.at
    event=monitor_refresh._current_wrh_monitor_observation_carrier(case.conn,case.position,now=at)
    assert event is not None
    hooks=[]
    with case.monkeypatch.context() as composition:
        composition.setattr(runtime,"process_current_global_batch",lambda events,**kwargs:hooks.append(kwargs) or
            SimpleNamespace(events=tuple(events),winner_event_id=None,receipts={}))
        bound=adapter.event_bound_live_adapter_from_trade_conn(case.trade,get_current_level=lambda:adapter.RiskLevel.GREEN,
            forecast_conn=case.conn,topology_conn=case.conn,calibration_conn=case.conn)
        bound.process_global_batch((event,),at)
    callbacks=hooks[-1]
    witness=current.snapshot.witness
    binding=next(b for b in witness.bindings if b.condition_id==case.position.condition_id)
    token=binding.no_token_id
    pairs={b.condition_id:(b.yes_token_id,b.no_token_id) for b in witness.bindings}
    def rebind(w):
        return universe._rebind_probability_witness_tokens(w,token_map_by_condition=pairs,
            required_token_ids=frozenset(t for pair in pairs.values() for t in pair))
    curve=S.ExecutableSellCurve(token_id=token,side="NO",snapshot_id="wrh-real-batch-book",book_hash="wrh-batch-book-hash",
        levels=(BidBookLevel(price=D(".07"),size=D("2")),),fee_model=FeeModel(fee_rate=D(".05")),
        min_tick=D(".01"),min_order_size=D("1"),quote_ttl=timedelta(seconds=30))
    states=tuple((witness.family_key,b.bin_id,b.condition_id,side,t,"NO_ASK",curve.book_hash,event.event_id,
        f"gamma-{b.condition_id}","False") for b in witness.bindings for side,t in (("YES",b.yes_token_id),("NO",b.no_token_id)))
    book=universe.CurrentGlobalBookEpoch(assets=(),sell_assets=(universe.CurrentGlobalSellAsset(
        family_key=witness.family_key,bin_id=binding.bin_id,condition_id=binding.condition_id,
        gamma_market_id=f"gamma-{binding.condition_id}",market_event_id=event.event_id,side="NO",token_id=token,
        curve=curve,captured_at_utc=at,neg_risk=False),),asset_states=states,captured_at_utc=at,
        max_age=timedelta(seconds=30),witness_identity=universe.current_global_book_epoch_identity(asset_states=states,captured_at_utc=at))
    wealth=source_harness._test_wealth_witness(ledger_snapshot_id="wrh-execution-ledger",position_set_hash="wrh-held-five-no",
        wealth_floor_usd=D(cash),wealth_ceiling_usd=D(cash)+5,spendable_cash_usd=D(cash),reservations_usd=D("0"),
        collateral_authority="CHAIN",captured_at_utc=at,max_age=timedelta(seconds=30),
        native_holdings_micro=((token,5_000_000),),native_commitments_micro=((token,600_000),))
    scope=universe.current_global_auction_scope_from_events((event,),captured_at_utc=at)
    selections,actuations=[],[]
    def capture(_event,actuation,*_args):
        actuations.append(actuation)
        return runtime.GlobalWinnerPreflight(status="BATCH_BLOCKED",reason="OFFLINE_WRH_EXECUTOR_CONTINUATION")
    with case.monkeypatch.context() as inputs:
        inputs.setattr(runtime,"scan_current_global_auction_scope",lambda **_:scope)
        inputs.setattr(runtime,"current_portfolio_wealth_witness",lambda *_a,**_k:wealth)
        inputs.setattr(runtime,"current_venue_auction_identity",lambda *_a,**_k:book.witness_identity)
        batch=runtime.process_current_global_batch((event,),decision_time=at,world_conn=case.conn,
            forecast_conn=case.conn,trade_conn=case.trade,payload_reader=lambda e:json.loads(e.payload_json),
            prepare_event=callbacks["prepare_event"],prepare_held_event=callbacks["prepare_held_event"],
            actuate_winner=lambda *_:pytest.fail("only fake-venue continuation may actuate"),stamp_receipt=lambda r:r,
            venue_submit_count=lambda:0,current_execution=lambda *_:None,current_time_provider=lambda:at,
            portfolio_state_provider=lambda:PortfolioState(positions=[case.position],authority="canonical_db",authority_scope="runtime_exposure"),
            current_book_epoch_provider=lambda probabilities,cut:({k:rebind(w) for k,w in probabilities.items()},book),
            current_capital_limit_resolver=lambda *_:D(cash),preflight_winner=capture,
            actuate_preflighted_winner=lambda *_:pytest.fail("handoff is captured"),
            selection_telemetry_observer=lambda p,b,f,s,c:selections.append(s))
    assert actuations,{key:value.reason for key,value in batch.receipts.items()}
    ranked=replace(selections[-1],actuation=actuations[-1])
    selected=ranked.decision.candidate
    probability=ranked.actuation.probability_witness
    q=S.family_payoff_point_q(probability,bin_id=selected.bin_id,side=selected.side)
    assert q==pytest.approx(current.held_q)
    assert ranked.actuation.auction_receipt_ref is not None
    return SimpleNamespace(ranked=ranked,selected=selected,probability=probability,q=q,curve=curve,wealth=wealth,event=event)


def test_native_wrh_selected_sale_reaches_confirmed_fill_within_book_window(wrh_case,record_property):
    before=_advance(wrh_case,(26.,25.))
    assert before.selection.candidate is None
    current=_advance(wrh_case,(30.,25.),minute=1)
    assert before.state.value_native==current.state.value_native==25.
    assert before.state.observed_at==current.state.observed_at
    assert before.state.identity()!=current.state.identity()
    assert before.fact["observed_extreme_native"]==26.
    assert current.fact["observed_extreme_native"]==30.
    assert 0 < current.held_q < before.held_q < 1
    handoff=_real_batch_handoff(wrh_case,current)
    assert handoff.selected.action=="SELL" and handoff.selected.execution_mode=="TAKER_LIMIT"
    assert handoff.ranked.decision.shares==D("2")
    execution=_confirmed_wrh_execution(wrh_case,current,handoff)
    assert current.at==before.at+timedelta(seconds=60)
    assert execution.at==before.at+timedelta(seconds=61)
    assert execution.window_end==before.at+timedelta(seconds=120)
    assert execution.at < execution.window_end
    record_property("native_wrh_confirmed_exit",json.dumps({
        "before_q":before.held_q,"current_q":current.held_q,
        "source_revision":current.state.source_revision_identity,
        "decision_at":current.at.isoformat(),"confirmed_at":execution.at.isoformat(),
        "book_window_end":execution.window_end.isoformat(),"price":".07",
        "confirmed_shares":"2","residual_shares":"3","fee_paid_micro":6510,
        "gross_realized_pnl":"-.10","fee_net_proceeds":".13349","fee_net_realized_pnl":"-.10651"}))


def _confirmed_wrh_execution(case,current,handoff):
    """Preserve the selected WRH world through actual JIT/lifecycle/executor."""
    from src.engine import event_reactor_adapter as adapter
    from src.execution import exit_lifecycle,executor
    from src.state import collateral_ledger
    from src.state.collateral_ledger import CollateralLedger,CollateralSnapshot,init_collateral_schema
    from src.state.snapshot_repo import insert_snapshot
    from src.state.portfolio import ExitContext,PortfolioState,load_runtime_open_portfolio
    from src.state.venue_command_repo import append_trade_fact
    from tests.integration import test_w3_solve_seam_g3 as source_harness
    from tests import test_exit_safety as execution_harness
    import numpy as np

    at=current.at+timedelta(seconds=1)
    window_end=case.request.computed_at+timedelta(seconds=120)
    case.clock[0]=at
    # Reproduce the frozen selection from current canonical source/forecast
    # truth through the same reproof used by the production SELL wrapper.
    # This carrier is intentionally ephemeral; no WORLD event reload is a
    # substitute for the native current product.
    prepared,_=adapter._current_global_actuation_prepared_family(handoff.event,
        global_actuation=handoff.ranked.actuation,forecast_conn=case.conn,
        topology_conn=case.conn,observation_conn=case.conn,decision_time=at)
    assert not adapter._global_probability_action_content_mismatches(
        prepared.probability_witness,handoff.probability)
    assert case.conn.execute("SELECT 1 FROM world.opportunity_events WHERE event_id=?",
        (handoff.event.event_id,)).fetchone() is None
    binding=next(b for b in handoff.probability.bindings if b.condition_id==handoff.selected.condition_id)
    market=source_harness._jit_market_authority(handoff.selected,tick=".01",min_order_size="1")
    raw_book={"asset_id":handoff.selected.token_id,"tick_size":".01","min_order_size":"1",
        "bids":[{"price":".07","size":"2"}],"asks":[{"price":".08","size":"5"}]}
    native_snapshot=replace(market.snapshot,captured_at=at,freshness_deadline=window_end,
        yes_token_id=binding.yes_token_id,no_token_id=binding.no_token_id,
        token_map_raw={"YES":binding.yes_token_id,"NO":binding.no_token_id},
        orderbook_top_bid=D(".07"),orderbook_top_ask=D(".08"),orderbook_depth_jsonb=json.dumps(raw_book))
    market=replace(market,snapshot=native_snapshot)
    rebound=adapter._global_sell_candidate_from_raw_book(handoff.selected,raw_book,captured_at_utc=at,market_authority=market)
    authority=exit_lifecycle.GlobalSellExecutionAuthority.from_current(actuation=handoff.ranked.actuation,jit_candidate=rebound)
    native_snapshot=replace(native_snapshot,raw_orderbook_hash=rebound.executable_sell_curve.book_hash)
    insert_snapshot(case.trade,native_snapshot)
    case.trade.commit()
    projection=SimpleNamespace(ranked=handoff.ranked,authority=authority,rebound=rebound,snapshot=native_snapshot)
    intent,_=source_harness._normal_hko_sell_intents(projection)
    price=authority.limit_price()
    receipt=adapter._global_sell_probability_receipt(candidate=handoff.selected,witness=handoff.probability,
        held_side_probability=handoff.q)
    intent=replace(intent,best_bid=float(price),current_market_price=float(price),probability_receipt=receipt)

    class EventClock(datetime):
        @classmethod
        def now(cls,tz=None): return at.astimezone(tz) if tz else at.replace(tzinfo=None)
    case.monkeypatch.setattr(executor,"datetime",EventClock)
    case.monkeypatch.setattr(collateral_ledger,"datetime",EventClock)
    case.monkeypatch.setattr(execution_harness,"datetime",EventClock)
    case.monkeypatch.setattr(exit_lifecycle,"_utcnow",lambda:at)
    execution_harness._enable_exit_submit_prereqs(case.trade,case.monkeypatch,ctf_shares=5)
    init_collateral_schema(case.trade)
    CollateralLedger(case.trade).set_snapshot(CollateralSnapshot(pusd_balance_micro=1_000_000_000,
        pusd_allowance_micro=1_000_000_000,usdc_e_legacy_balance_micro=0,
        ctf_token_balances={handoff.selected.token_id:5_000_000},
        ctf_token_allowances={handoff.selected.token_id:5_000_000},reserved_pusd_for_buys_micro=0,
        reserved_tokens_for_sells={},captured_at=at,authority_tier="CHAIN"))
    case.monkeypatch.setattr("src.data.polymarket_client.resolve_funder_address",lambda:"0x"+"12"*20)
    calls=[]
    class FakeVenue:
        def _ensure_v2_adapter(self): return self
        def get_ctf_collateral_payload(self,*,token_ids):
            assert token_ids==[handoff.selected.token_id]
            return execution_harness._fresh_exit_collateral_payload(token_id=handoff.selected.token_id,shares=5)
        def get_collateral_payload(self):
            return execution_harness._fresh_exit_collateral_payload(token_id=handoff.selected.token_id,shares=5)
        def bind_submission_envelope(self,envelope): self.envelope=envelope
        def bind_signed_submission_identity_persister(self,persister): self.persister=persister
        def place_limit_order(self,**order):
            assert at<window_end
            assert order["side"]=="SELL" and order["order_type"]=="FAK"
            assert order["token_id"]==handoff.selected.token_id
            assert D(str(order["price"]))==D(".07") and order["size"]==pytest.approx(2)
            assert case.trade.execute("SELECT state FROM venue_commands WHERE side='SELL'").fetchone()[0]=="SUBMITTING"
            calls.append(order)
            raw={"success":True,"status":"MATCHED","orderID":"wrh-confirmed-exit","matchedSize":"2",
                "avgPrice":".07","tradeIDs":["wrh-exit-trade"]}
            envelope=self.envelope.with_updates(order_id=raw["orderID"],raw_response_json=json.dumps(raw,sort_keys=True,separators=(",",":")))
            return {**raw,"_venue_submission_envelope":envelope.to_dict()}
    case.monkeypatch.setattr("src.data.polymarket_client.PolymarketClient",FakeVenue)
    real_execute=executor.execute_exit_order
    confirmed=[]
    def private_execute(order_intent,**kwargs):
        result=real_execute(order_intent,conn=case.trade,**kwargs)
        assert result.status=="filled",result.reason
        assert result.venue_call_started and result.venue_ack_received
        append_trade_fact(case.trade,trade_id="wrh-exit-trade",venue_order_id=result.order_id,command_id=result.command_id,
            state="CONFIRMED",filled_size="2",fill_price=".07",fee_paid_micro=6510,source="FAKE_VENUE",
            observed_at=at.isoformat(),venue_timestamp=at.isoformat(),raw_payload_hash=hashlib.sha256(b"wrh-confirmed").hexdigest(),
            raw_payload_json={"status":"CONFIRMED","test_only":True})
        case.trade.commit();confirmed.append(result)
        return result
    case.monkeypatch.setattr(exit_lifecycle,"execute_exit_order",private_execute)
    try:
        held=load_runtime_open_portfolio(case.trade).positions[0]
        held_samples=__import__("src.solve.solver",fromlist=["family_payoff_q_samples"]).family_payoff_q_samples(
            handoff.probability,bin_id=handoff.selected.bin_id,side="NO")
        outcome=exit_lifecycle.execute_exit(PortfolioState(positions=[held]),held,
            ExitContext(exit_reason="GLOBAL_CAPITAL_OPTIMAL_SELL",fresh_prob=handoff.q,fresh_prob_is_fresh=True,
                current_ci=tuple(float(v) for v in np.quantile(held_samples,(.05,.95))),
                current_market_price=.07,current_market_price_is_fresh=True,best_bid=.07,best_ask=.08,min_tick=.01,
                hours_to_settlement=22,position_state="day0_window",probability_receipt=receipt),
            clob=SimpleNamespace(get_order_status=lambda _: {"status":"CONFIRMED","matched_size":"2","remaining_size":"0","avgPrice":".07"}),
            conn=case.trade,exit_intent=intent,global_sell_authority=authority,
            global_sell_required_snapshot_id=native_snapshot.snapshot_id,global_sell_prefetched_orderbook=raw_book)
        assert outcome.startswith("position_reduced:"),outcome
        assert len(calls)==len(confirmed)==1
        row=case.trade.execute("SELECT phase,shares,realized_pnl_usd FROM position_current WHERE position_id=?",(held.trade_id,)).fetchone()
        assert row["phase"]=="day0_window" and row["shares"]==pytest.approx(3)
        # The existing lifecycle projection is fee-exclusive (also on base
        # 620a); keep the exact confirmed fee fact and net economics separate.
        assert row["realized_pnl_usd"]==pytest.approx(-.10)
        from src.state.fill_dedup import economic_trade_facts_for_command
        facts=economic_trade_facts_for_command(case.trade,confirmed[0].command_id)
        assert len(facts)==1 and facts[0]["state"]=="CONFIRMED"
        assert facts[0]["fee_paid_micro"]==6510
        net=D(str(facts[0]["filled_size"]))*D(str(facts[0]["fill_price"]))-D(facts[0]["fee_paid_micro"])/D(1_000_000)
        assert net==D(".13349") and net-D(".24")==D("-.10651")
        assert handoff.ranked.decision.expected_terminal_wealth.expected_ev_usd==pytest.approx(float(net)-2*handoff.q)
        with pytest.raises(ValueError,match="GLOBAL_SELL_POSITION_SHARES_SUPERSEDED|GLOBAL_SELL_POSITION_EXIT_ALREADY_ACTIVE"):
            adapter._current_global_sell_position(case.trade,handoff.selected)
        assert len(calls)==1
        return SimpleNamespace(outcome=outcome,confirmed=confirmed[0],window_end=window_end,at=at,remaining=dict(row))
    finally:
        execution_harness._clear_exit_submit_prereqs()
