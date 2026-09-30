# Created: 2026-09-29
# Last reused/audited: 2026-09-30
# Authority: REQ-20260930-114240-ee2a70; isolated observation/auction/executor integration.
"""Controlled forecast inputs, real Day0 integration and posterior persistence.

This harness is not production evidence: external forecasts and venue responses
are fixtures. The posterior calculation and semantic readiness checks are real.
"""
from dataclasses import asdict, replace
from contextlib import nullcontext
from decimal import Decimal
import sqlite3
from datetime import datetime,timedelta,timezone
import json
import logging
import time
from types import SimpleNamespace
import pytest

from tests import test_replacement_forecast_materializer as fixtures
from tests import test_replacement_forecast_bundle_reader as reader_fixtures
from tests import test_exit_safety as exit_fixtures
from src.data.day0_hourly_vectors import Day0HourlyVector, day0_source_clock_ensemble_member_models
from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live
from src.data.replacement_forecast_bundle_reader import read_replacement_forecast_bundle
from src.runtime.observation_reaction_trace import (
    completed_trace, emit_posterior_ready, emit_stage, emit_venue_ack,
)

_materializer_unit_source_surface = fixtures._materializer_unit_source_surface
trade_schema = exit_fixtures.conn


def _auction_from_served_observation(bundle, *, at, trade):
    """Real coordinator/solver over a complete controlled two-family universe.

    The changed family's point probabilities are the exact served posterior.
    Books, flat wealth, peer-family probabilities and repeated confidence draws
    are controlled harness inputs, not claims about a production order book.
    """
    import numpy as np
    from tests.integration import test_w3_solve_seam_g3 as g
    from src.engine.qkernel_spine_bridge import PreparedGlobalFamily
    from src.events.opportunity_event import make_opportunity_event
    from src.solve.solver import JointOutcomeProbabilityWitness, OutcomeTokenBinding

    events=[]
    for city, identity in ((bundle.city,bundle.posterior_identity_hash),('London','peer-fixture')):
        template=g._global_scope_event(city=city,source_run_id=identity)
        payload=json.loads(template.payload_json)
        payload.update(target_date=bundle.target_date,city_timezone='Asia/Shanghai' if city==bundle.city else 'Europe/London',
                       captured_at=at.isoformat(),available_at=at.isoformat(),snapshot_hash=identity,
                       snapshot_id='fixture-'+identity,cycle=bundle.source_cycle_time)
        events.append(make_opportunity_event(event_type='FORECAST_SNAPSHOT_READY',
            entity_key=f'{city}|{bundle.target_date}|high',source='observation-auction-harness',
            observed_at=at.isoformat(),available_at=at.isoformat(),received_at=at.isoformat(),
            payload=payload,causal_snapshot_id=payload['snapshot_id']))
    scope=g.current_global_auction_scope_from_events(tuple(events),captured_at_utc=at)
    wealth=g._test_wealth_witness(ledger_snapshot_id='auction-ledger',position_set_hash='flat',
        wealth_floor_usd=Decimal('1000'),wealth_ceiling_usd=Decimal('1000'),
        spendable_cash_usd=Decimal('1000'),reservations_usd=Decimal('0'),
        collateral_authority='CHAIN',captured_at_utc=at,max_age=timedelta(seconds=30))
    prepared={};probabilities={};assets=[]
    for family,event in scope.events_by_family:
        changed=json.loads(event.payload_json)['city']==bundle.city
        bins=tuple(sorted(bundle.q))
        point=np.asarray([bundle.q[b] for b in bins] if changed else [1/len(bins)]*len(bins))
        bindings=tuple(OutcomeTokenBinding(b,f'{family}:{b}',f'{family}:{b}:YES',f'{family}:{b}:NO') for b in bins)
        identity=bundle.posterior_identity_hash if changed else 'peer-fixture'
        fields=dict(family_key=family,bindings=bindings,yes_point_q=point,
            yes_q_samples=np.tile(point,(400,1)),q_version=identity,
            resolution_identity='fixture-resolution:'+family,topology_identity=bundle.bin_topology_hash,
            posterior_identity_hash=identity,source_truth_identity='input:'+identity,
            authority_certificate_hash='fixture-certificate:'+identity,band_alpha=.05,
            band_basis='PARAMETER_POSTERIOR_SIMPLEX_V1',captured_at_utc=at)
        witness=JointOutcomeProbabilityWitness(**fields,max_age=timedelta(seconds=30),
            witness_identity=g.joint_probability_witness_identity(**fields))
        probabilities[family]=witness
        prepared[event.event_id]=PreparedGlobalFamily(decision_id=event.event_id,
            probability_witness=witness,candidate_seeds=(),posterior_id=bundle.posterior_id if changed else None)
        for binding in bindings:
            for side,token in (('YES',binding.yes_token_id),('NO',binding.no_token_id)):
                price=Decimal('.40') if changed and binding.bin_id=='hot' and side=='YES' else Decimal('.95')
                curve=g.ExecutableCostCurve(token_id=token,side=side,snapshot_id='book:'+token,
                    book_hash='hash:'+token,levels=(g.BookLevel(price=price,size=Decimal('100')),),
                    fee_model=g.FeeModel(fee_rate=Decimal('0')),min_tick=Decimal('.01'),
                    min_order_size=Decimal('1'),quote_ttl=timedelta(seconds=30))
                assets.append(g.CurrentGlobalBookAsset(family_key=family,bin_id=binding.bin_id,
                    condition_id=binding.condition_id,gamma_market_id='gamma:'+binding.condition_id,
                    market_event_id=event.event_id,side=side,token_id=token,curve=curve,
                    captured_at_utc=at,neg_risk=False,
                    bid_levels=(g.BidBookLevel(price=Decimal('.39'),size=Decimal('100')),)))
    prepared=g.global_batch_runtime._bind_selection_holdings(prepared,
        portfolio_state=SimpleNamespace(positions=()),wealth_witness=wealth)
    states=tuple((a.family_key,a.bin_id,a.condition_id,a.side,a.token_id,'EXECUTABLE',
                  a.curve.book_hash,a.market_event_id,a.gamma_market_id,str(a.neg_risk)) for a in assets)
    book_id=g.current_global_book_epoch_identity(asset_states=states,captured_at_utc=at)
    book=g.CurrentGlobalBookEpoch(assets=tuple(assets),asset_states=states,captured_at_utc=at,
                                 max_age=timedelta(seconds=30),witness_identity=book_id)
    started=time.monotonic_ns()
    result=g.select_prepared_global_auction(prepared,
        selection_epoch_identity='source-cut:'+bundle.posterior_identity_hash,
        selection_cut_at_utc=at,current_scope=scope,
        current_scope_identity_resolver=lambda:scope.scope_identity,
        venue_universe_identity=book_id,current_venue_universe_identity_resolver=lambda:book_id,
        universe_max_age=timedelta(seconds=30),
        current_probability_resolver=lambda key:g.CurrentFamilyProbabilityAuthority.from_witness(probabilities[key]),
        current_execution_resolver=lambda c:g.CurrentExecutionAuthority(token_id=c.token_id,side=c.side,
            book_snapshot_id=c.book_snapshot_id,execution_curve_identity=c.execution_curve_identity,
            action=getattr(c,'action','BUY'),neg_risk=c.neg_risk),
        current_wealth_identity_resolver=lambda:wealth.economic_identity,
        wealth_witness=wealth,capital_limit_usd=Decimal('100'),fractional_kelly_multiplier=Decimal('.25'),
        decision_at_utc=at,book_epoch=book)
    elapsed=(time.monotonic_ns()-started)/1e6
    assert result.decision.candidate is not None,result.decision
    winner=result.decision.candidate
    assert isinstance(winner,g.GlobalSingleOrderCandidate) and winner.bin_id=='hot' and winner.side=='YES'
    assert probabilities[winner.family_key].q_version==bundle.posterior_identity_hash
    assert len(probabilities)==2 and len(assets)==12
    receipt_id=g.global_batch_runtime._store_global_auction_receipt(trade,selected=result,
        selection_epoch_identity=result.actuation.selection_epoch_identity,
        selection_cut_at_utc=at,decision_at_utc=at,
        probability_manifest=tuple((key,w.witness_identity) for key,w in probabilities.items()),
        full_scope_identity=scope.scope_identity,full_scope_family_keys=tuple(probabilities),
        probability_ineligible_by_family={},book_epoch_identity=book_id,
        book_asset_count=len(assets),book_asset_states=states,wealth_witness=wealth,
        fractional_kelly_multiplier=Decimal('.25'),book_captured_at_utc=at,
        book_max_age=timedelta(seconds=30),probability_witnesses=probabilities,book_epoch=book,
        buy_candidates_enabled=True)
    assert receipt_id is not None
    trade.commit()
    result=g.global_batch_runtime._bind_stored_global_auction_receipt(trade,
        selected=result,decision_log_id=receipt_id)
    return result,elapsed


def _seed_prior_rest(trade, forecasts, *, city, target_date, at):
    """A previous run's already-acknowledged order is an explicit input fixture."""
    from tests import test_executor as entry
    from tests import test_venue_command_repo as commands
    from src.state.venue_command_repo import insert_submission_envelope
    token='prior-rest-token';command='prior-rest';venue='prior-venue'
    stamp=(at-timedelta(minutes=1)).isoformat()
    sid=entry._ensure_snapshot(trade,token_id=token,condition_id='cond-'+token,
        snapshot_id='prior-rest-book',final_limit_price=Decimal('.50'))
    envelope=commands._make_envelope(token_id=token,price=Decimal('.50'),size=Decimal('10'))
    envelope=envelope.with_updates(condition_id='cond-'+token,order_type='GTC',post_only=True)
    insert_submission_envelope(trade,envelope,envelope_id='prior-rest-envelope')
    trade.execute('INSERT INTO venue_commands '
        '(command_id,snapshot_id,envelope_id,position_id,decision_id,idempotency_key,intent_kind,'
        'market_id,token_id,side,size,price,venue_order_id,state,created_at,updated_at,q_version) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (command,sid,'prior-rest-envelope','prior-position','prior-decision','prior-idempotency',
         'ENTRY','cond-'+token,token,'BUY',10,.50,venue,'ACKED',stamp,stamp,'a'*64))
    trade.execute('INSERT INTO venue_order_facts '
        '(venue_order_id,command_id,state,remaining_size,matched_size,source,observed_at,local_sequence,raw_payload_hash) '
        "VALUES (?,?,'LIVE','10','0','FAKE_VENUE',?,1,?)",(venue,command,stamp,'f'*64))
    trade.execute('INSERT INTO venue_command_events '
        '(event_id,command_id,sequence_no,event_type,occurred_at,payload_json,state_after) '
        "VALUES ('prior-acked',?,1,'SUBMIT_ACKED',?,?,'ACKED')",(command,stamp,json.dumps({'venue_order_id':venue})))
    forecasts.execute('INSERT INTO market_events '
        '(market_slug,city,target_date,temperature_metric,condition_id,token_id) VALUES (?,?,?,?,?,?)',
        ('prior-rest-market',city,target_date,'high','cond-'+token,token))
    trade.commit();forecasts.commit()


def _cancel_prior_rest(trade, forecasts, *, bundle, at):
    """Real C3 classification/batch cancellation; denied budget cannot authorize replacement."""
    from src.execution.staleness_cancel import run_c3_staleness_cancel_cycle, read_current_family_q_versions
    family=(bundle.city,bundle.target_date,bundle.temperature_metric)
    assert read_current_family_q_versions(forecasts,(family,),now=at)[family]==bundle.posterior_identity_hash
    class Venue:
        def __init__(self): self.calls=[]
        def cancel_orders_batch(self, ids):
            assert ids==['prior-venue']
            assert trade.execute("SELECT state FROM venue_commands WHERE command_id='prior-rest'").fetchone()[0]=='CANCEL_PENDING'
            self.calls.append(ids)
            return [{'canceled':True,'orderID':'prior-venue'}]
    class Budget:
        allowed=False
        def try_acquire(self,_request_class):
            return SimpleNamespace(granted=self.allowed,decision=SimpleNamespace(value='DENIED'))
    venue=Venue();budget=Budget();started=time.monotonic_ns()
    denied=run_c3_staleness_cancel_cycle(trade,trade,forecasts,venue,now=at,rate_budget=budget)
    assert denied['cancel_set_size']==1,denied
    assert not denied['confirmed_families'] and not venue.calls
    assert trade.execute('SELECT COUNT(*) FROM venue_commands').fetchone()[0]==1
    budget.allowed=True
    confirmed=run_c3_staleness_cancel_cycle(trade,trade,forecasts,venue,now=at,rate_budget=budget)
    assert confirmed['confirmed_families']=={family},confirmed
    assert trade.execute("SELECT state FROM venue_commands WHERE command_id='prior-rest'").fetchone()[0]=='CANCELLED'
    trade.commit()
    assert venue.calls==[['prior-venue']]
    replay=run_c3_staleness_cancel_cycle(trade,trade,forecasts,venue,now=at,rate_budget=budget)
    assert replay['cancel_set_size']==0 and len(venue.calls)==1
    return (time.monotonic_ns()-started)/1e6


def _submit_selected_entry(trade, world, auction, bundle, monkeypatch):
    """Actual frozen-intent entry executor; operational health and venue are fixtures."""
    from tests import test_executor as entry
    from src.contracts import DecisionSourceContext
    from src.execution import executor
    from src.state.venue_command_repo import get_command, list_events

    decision=auction.decision; candidate=decision.candidate
    monkeypatch.setattr(entry,'_TEST_CONN',trade)
    source=DecisionSourceContext(source_id='replacement_0_1',model_family='replacement_0_1',
        forecast_issue_time=bundle.source_cycle_time,forecast_valid_time=bundle.target_date+'T00:00:00+00:00',
        forecast_fetch_time=bundle.computed_at,forecast_available_at=bundle.source_available_at,
        raw_payload_hash=bundle.posterior_identity_hash,posterior_identity_hash=bundle.posterior_identity_hash,
        degradation_level='OK',forecast_source_role='entry_primary',authority_tier='FORECAST',
        decision_time=bundle.computed_at,decision_time_status='OK',
        polymarket_end_anchor_source='gamma_explicit',
        first_member_observed_time=fixtures._dt(2).isoformat(),run_complete_time=fixtures._dt(2).isoformat())
    entry._ensure_snapshot(trade,token_id=candidate.token_id,condition_id=candidate.condition_id,
        snapshot_id='selected-auction-book',final_limit_price=decision.limit_price,
        snapshot_top_ask=decision.limit_price,snapshot_top_bid=Decimal('.39'))
    intent=entry._final_execution_intent(token_id=candidate.token_id,direction='buy_yes',
        size_kind='shares',size_value=decision.shares,submitted_shares=decision.shares,
        final_limit_price=decision.limit_price,expected_fill_price_before_fee=decision.expected_fill_price_before_fee,
        order_type='FOK',post_only=False,snapshot_top_ask=decision.limit_price,
        snapshot_top_bid=Decimal('.39'),snapshot_id='selected-auction-book',
        resolution_window=bundle.target_date,correlation_key=candidate.family_key,
        decision_source_context=source)
    from src.engine.event_reactor_adapter import (_build_event_bound_taker_quality_proof,
        _global_current_state_execution_economics,CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION)
    act=auction.actuation
    cert={'source':'qkernel_spine','decision_id':act.actuation_identity,
        'receipt_hash':act.auction_receipt_ref.receipt_hash,'side':candidate.side,
        'candidate_id':candidate.candidate_id,'bin_id':candidate.bin_id,'route_id':'native_taker',
        'global_auction_receipt':act.auction_receipt_ref.as_payload(),
        'global_selection_revision':CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION,
        'global_candidate_id':candidate.candidate_id,'global_execution_mode':candidate.execution_mode,
        'global_condition_id':candidate.condition_id,'global_token_id':candidate.token_id,
        'global_family_key':candidate.family_key,'global_bin_id':candidate.bin_id,
        'global_probability_witness_identity':candidate.probability_witness_identity,
        'global_probability_authority':'replacement','global_posterior_id':bundle.posterior_id,
        'global_book_hash':candidate.executable_cost_curve.book_hash,
        'global_jit_book_hash':candidate.executable_cost_curve.book_hash,
        'global_jit_venue_book_hash':candidate.executable_cost_curve.book_hash,
        'global_jit_book_snapshot_id':candidate.book_snapshot_id,
        'global_jit_execution_curve_identity':candidate.execution_curve_identity,
        'global_target_shares':str(decision.shares),'global_expected_cost_usd':str(decision.cost_usd),
        'global_limit_price':str(decision.limit_price),
        'global_expected_fill_price_before_fee':str(decision.expected_fill_price_before_fee),
        'global_max_spend_usd':str(decision.max_spend_usd),'global_optimum_semantics':'CUT_TIME_GLOBAL_OPTIMUM',
        'global_current_token_shares':str(decision.current_token_shares),
        'global_full_kelly_target_shares':str(decision.full_kelly_target_shares),
        'global_fractional_kelly_target_shares':str(decision.fractional_kelly_target_shares),
        'global_buy_sizing_mode':str(decision.buy_sizing_mode),
        'optimal_delta_u':decision.expected_growth.expected_delta_log_wealth,
        'direction_law_ok':True,'coherence_allows':True}
    for field in ['actuation_identity','winner_event_id','economic_identity','universe_witness_identity',
                  'wealth_witness_identity','wealth_economic_identity','selection_epoch_identity']:
        cert['global_'+field]=getattr(act,field)
    cert['global_selection_cut_at']=act.selection_cut_at_utc.isoformat()
    cert['global_selection_decision_at']=act.decision_at_utc.isoformat()
    growth=decision.expected_growth
    assert growth is not None and decision.expected_terminal_wealth is not None
    cert.update(global_utility_basis=growth.utility_basis,
        global_proposal_expected_delta_log_wealth=growth.expected_delta_log_wealth,
        global_proposal_expected_ev_usd=growth.expected_ev_usd,
        global_proposal_expected_log_growth_per_hour=growth.expected_log_growth_per_hour,
        global_proposal_expected_capital_efficiency=growth.expected_capital_efficiency,
        global_proposal_capital_lock_hours=growth.capital_lock_hours,
        global_ruin_probability_reduction=growth.ruin_probability_reduction,
        global_terminal_ruin_probability_reduction=decision.expected_terminal_wealth.ruin_probability_reduction,
        global_proposal_fill_semantics='IMMEDIATE_FILL',global_fill_probability=1.)
    economics=_global_current_state_execution_economics(cert,decision=decision,
        witness=act.probability_witness,decision_time=act.decision_at_utc)
    from src.decision_kernel.canonicalization import qkernel_global_current_state_rejection_reason
    assert qkernel_global_current_state_rejection_reason(economics) is None, qkernel_global_current_state_rejection_reason(economics)
    q=float(economics['payoff_q_action']);lcb=float(economics['payoff_q_lcb'])
    actionable={
        'direction':'buy_yes','q_live':q,'q_lcb_5pct':lcb,'candidate_id':candidate.candidate_id,
        'candidate_bin_id':candidate.bin_id,'selection_authority_applied':'qkernel_spine',
        'qkernel_execution_economics':economics,
        'global_auction_receipt':act.auction_receipt_ref.as_payload(),
        'live_cap_reserved_notional_usd':float(decision.cost_usd),
        'strategy_key':'forecast_qkernel_entry','min_entry_price':.05,
        'min_expected_profit_usd':.05,'min_submit_edge_density':.02,
    }
    quality=_build_event_bound_taker_quality_proof(actionable_payload=actionable,
        order_mode='TAKER',fresh_best_bid=.39,fresh_best_ask=float(decision.limit_price))
    assert quality is not None and quality['passed'],json.dumps(quality)
    intent=replace(intent,hypothesis_id='observed-auction-selection',q_live=q,q_lcb_5pct=lcb,
        expected_edge=q-float(decision.expected_fill_price_before_fee),qkernel_execution_economics=economics,
        taker_quality_proof=quality,selection_authority_applied='qkernel_spine',
        min_entry_price=.05,min_expected_profit_usd=.05,min_submit_edge_density=.02)
    # Parent acquisition is a harness boundary. Payload verification, canonical
    # hash construction and the executor's durable certificate reread are real.
    from tests.execution.test_entry_actionable_certificate_guard import _valid_actionable_payload
    from src.decision_kernel.certificate import build_certificate
    from src.decision_kernel.ledger import DecisionCertificateLedger
    from src.decision_kernel.verifier import _verify_actionable_payload
    payload=_valid_actionable_payload()
    payload.update(actionable,event_id=auction.winner_event_id,causal_snapshot_id=str(bundle.posterior_id),
        family_id=candidate.family_key,condition_id=candidate.condition_id,token_id=candidate.token_id,
        executable_snapshot_id=intent.snapshot_id,kelly_size_usd=float(decision.cost_usd),
        c_fee_adjusted=float(decision.limit_price),c_cost_95pct=float(decision.limit_price),p_fill_lcb=1.,
        trade_score=q-float(decision.limit_price),action_score=decision.expected_growth.expected_delta_log_wealth)
    _verify_actionable_payload(SimpleNamespace(payload=payload))
    certificate=build_certificate(certificate_type='ActionableTradeCertificate',
        semantic_key='harness:'+candidate.candidate_id,claim_type='actionable_trade',mode='LIVE',
        decision_time=datetime.fromisoformat(bundle.computed_at),payload=payload,
        authority_id='controlled-harness-parent',authority_version='1',algorithm_id='global-single-order',algorithm_version='1')
    DecisionCertificateLedger(world).insert_idempotent(certificate,preverified=True)
    world.commit()
    intent=replace(intent,actionable_certificate_hash=certificate.certificate_hash)
    monkeypatch.setattr('src.state.db.get_world_connection_read_only',
        lambda:sqlite3.connect(f'file:{world.execute("PRAGMA database_list").fetchone()[2]}?mode=ro',uri=True))
    # No selection/ranking, probability, command or snapshot check is replaced.
    # The process-health fixture is explicit, as in executor integration tests.
    monkeypatch.setattr(executor,'_assert_risk_allocator_allows_submit',lambda _intent:None)
    monkeypatch.setattr(executor,'_select_risk_allocator_order_type',lambda *_a,**_k:'FOK')
    monkeypatch.setattr(executor,'_entry_replacement_family_from_snapshot',
        lambda *_a,**_k:(bundle.city,bundle.target_date,bundle.temperature_metric))
    submits=[]
    class FakeEntryVenue:
        def bind_submission_envelope(self,envelope):self.envelope=envelope
        def bind_signed_submission_identity_persister(self,persister):self.persister=persister
        def v2_preflight(self):return None
        def get_collateral_payload(self):return exit_fixtures._fresh_exit_collateral_payload()
        def place_limit_order(self,**kwargs):
            command=trade.execute("SELECT state,q_version FROM venue_commands WHERE decision_id='selected-observation-entry'").fetchone()
            assert command is not None and command['state']=='SUBMITTING'
            assert command['q_version']==bundle.posterior_identity_hash
            assert kwargs['side']=='BUY' and kwargs['token_id']==candidate.token_id
            assert Decimal(str(kwargs['price']))==decision.limit_price
            assert Decimal(str(kwargs['size']))==decision.shares
            prior=trade.execute("SELECT state FROM venue_commands WHERE command_id='prior-rest'").fetchone()
            assert prior is None or prior[0]=='CANCELLED'
            submits.append(kwargs)
            return entry._final_submit_result(self.envelope,order_id='observed-auction-entry')
    monkeypatch.setattr('src.data.polymarket_client.PolymarketClient',FakeEntryVenue)
    # Exercise the production cross-DB admission context on isolated paths.
    from src.state import db as state_db
    from pathlib import Path
    trade_path=Path(trade.execute('PRAGMA database_list').fetchone()[2])
    world_path=Path(world.execute('PRAGMA database_list').fetchone()[2])
    monkeypatch.setattr(state_db,'_zeus_trade_db_path',lambda:trade_path)
    monkeypatch.setattr(state_db,'ZEUS_WORLD_DB_PATH',world_path)
    def entry_connection(**_kwargs):
        c=sqlite3.connect(trade_path);c.row_factory=sqlite3.Row
        return c
    monkeypatch.setattr(state_db,'get_trade_connection',entry_connection)
    trade.commit()
    with state_db.trade_connection_with_world_flocked() as entry_conn:
        result=executor.execute_final_intent(intent,conn=entry_conn,decision_id='selected-observation-entry')
        entry_conn.commit()
    assert submits,result.reason
    trade.commit()
    assert get_command(trade,result.command_id)['state']=='ACKED',result
    return result,list_events(trade,result.command_id)


@pytest.mark.parametrize('reaction_path',['sell','entry','replace'])
@pytest.mark.parametrize('incumbent_without_carrier',[False,True])
def test_observation_revision_materializes_then_serves(monkeypatch,caplog,tmp_path,trade_schema,_materializer_unit_source_surface,incumbent_without_carrier,reaction_path):
    import scripts.materialize_replacement_forecast_live as cli
    import src.main as main  # Already resident in a warm trading process.
    from src.data import replacement_fusion_upgrade_trigger as delivery
    from src.runtime import reactor_wake
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.venue_command_repo import get_command, list_events
    from src.state.schema.observation_prints_schema import append_print, ensure_table

    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    forecast_path, world_path, trade_path = (tmp_path/name for name in ('zeus-forecasts.db','zeus-world.db','zeus_trades.db'))
    seed=fixtures._conn()
    conn=sqlite3.connect(forecast_path);conn.row_factory=sqlite3.Row
    seed.backup(conn);seed.close()
    trade=sqlite3.connect(trade_path);trade.row_factory=sqlite3.Row
    trade_schema.backup(trade)
    world=sqlite3.connect(world_path);ensure_table(world)
    from src.state.ledger import apply_architecture_kernel_schema
    apply_architecture_kernel_schema(world);world.commit()
    monkeypatch.setattr('src.state.db.get_world_connection',
        lambda *a,**kw: sqlite3.connect(world_path))
    # Production-like ownership: writes use distinct WORLD/FORECAST/TRADE files.
    # Only WORLD is attached read-only while preparing a posterior.
    conn.execute('ATTACH DATABASE ? AS world',(f'file:{world_path}?mode=ro',))
    exit_fixtures._enable_exit_submit_prereqs(trade,monkeypatch)
    snapshot_id=exit_fixtures._ensure_snapshot(trade,snapshot_id='source-reaction-book',
        orderbook_top_bid='0.74',orderbook_top_ask='0.75')
    trade.execute("INSERT INTO position_current(position_id,phase,market_id,city,target_date,temperature_metric,chain_state,chain_shares,chain_cost_basis_usd,updated_at) VALUES('source-reaction-held','active','condition-test','Shanghai','2026-06-07','high','synced',5,2,'2026-06-06T18:00:00Z')")
    trade.commit()
    source_models=('ecmwf_ifs9','gfs','icon','gem','jma')
    for index,model in enumerate(source_models):
        conn.execute('''INSERT INTO raw_model_forecasts
            (raw_model_forecast_id,model,city,target_date,metric,source_cycle_time,
             source_available_at,captured_at,lead_days,forecast_value_c,endpoint,recorded_at,coverage_status)
            VALUES(?,?,'Shanghai','2026-06-07','high',?,?,?,1,25.0,'single_runs',?,'COVERED')''',
            (101+index,model,fixtures._dt(0).isoformat(),fixtures._dt(3).isoformat(),
             fixtures._dt(3).isoformat(),fixtures._dt(3).isoformat()))
    from src.data.replacement_current_value_serving import read_current_instrument_values
    serving=read_current_instrument_values(conn,city='Shanghai',metric='high',
        target_date='2026-06-07',source_cycle_time_iso=fixtures._dt(0).isoformat(),
        decision_time_iso=fixtures._dt(4).isoformat())
    assert set(serving)==set(source_models)
    fixtures._install_live_fusion(monkeypatch,snapshot_id=1,
        current_serving={model:value.as_provenance() for model,value in serving.items()})
    original_override=fixtures.materializer_mod._replacement_bayes_precision_fusion_override
    monkeypatch.setattr(fixtures.materializer_mod,'_replacement_bayes_precision_fusion_override',
        lambda *args,**kw: replace(original_override(*args,**kw),
            raw_model_forecast_ids=tuple(range(101,106))))
    reader_fixtures._insert_ensemble_snapshot(conn,snapshot_id=1,
        source_cycle_time=fixtures._dt(0),available_at=fixtures._dt(2))
    # End fixture preparation before the independent WORLD evidence writer.
    conn.commit()
    if incumbent_without_carrier:
        incumbent=materialize_replacement_forecast_live(conn,fixtures._request())
        assert incumbent.ok,incumbent
        conn.commit()
        prior=conn.execute('SELECT provenance_json FROM forecast_posteriors WHERE posterior_id=?',(incumbent.posterior_id,)).fetchone()
        assert not json.loads(prior[0]).get('day0_current_temperature_state')
    now=fixtures._dt(18,10)
    request=fixtures._request(computed_at=now,expires_at=datetime(2026,6,7,2,tzinfo=timezone.utc),
        day0_observed_extreme_c=31.0,day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time=fixtures._dt(18,5).isoformat())
    if reaction_path=='replace':
        _seed_prior_rest(trade,conn,city=request.city,target_date=str(request.target_date),at=now)
    response_received_at_ms=time.time_ns()//1_000_000
    append_print(world,city='Shanghai',station_id='ZSPD',source_channel='aviationweather_metar',
        publish_ts_utc=fixtures._dt(18,5).isoformat(),value_native=30.0,unit='C',
        fetched_at_utc=fixtures._dt(18,5).isoformat(),raw_report='METAR ZSPD 061805Z 30/20 T03000200')
    world.commit()
    world_committed_at_ms=time.time_ns()//1_000_000
    input_identity={"source":"aviationweather_metar",
        "observed_at_utc":fixtures._dt(18,5).isoformat(),"value_native":30.0}
    emit_stage("SOURCE_COMMITTED",city="Shanghai",station_id="ZSPD",
        source_channel="aviationweather_metar",input_identity=input_identity,
        response_received_at_ms=response_received_at_ms,
        world_committed_at_ms=world_committed_at_ms)
    vector=Day0HourlyVector(model="ecmwf_ifs",city="Shanghai",target_date="2026-06-07",
        timezone_name="Asia/Shanghai",captured_at=fixtures._dt(18,8).isoformat(),
        times=tuple(f"2026-06-07T{hour:02d}:00" for hour in range(24)),
        temps_c=tuple(29.0 if hour<12 else 31.0 for hour in range(24)))
    def meta(model,ensemble=False):
        return json.dumps({"provider_source_cycle_time_utc":fixtures._dt(6).isoformat(),
            "provider_source_available_at_utc":fixtures._dt(7).isoformat(),
            "fetch_finished_at":fixtures._dt(18,9).isoformat(),
            "request_hash":"same-ensemble" if ensemble else model,
            "provider_run_id":"same-ensemble" if ensemble else model})
    providers=[replace(vector,source_run_meta_json=meta("ecmwf_ifs")),
               replace(vector,model="icon_global",source_run_meta_json=meta("icon_global"))]
    ensemble=[replace(vector,model=m,source_run_meta_json=meta(m,True)) for m in day0_source_clock_ensemble_member_models()]
    monkeypatch.setattr("src.data.day0_hourly_vectors.day0_hourly_models_for_city",lambda _:[v.model for v in providers])
    monkeypatch.setattr("src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
        lambda **kw:ensemble if len(kw.get("expected_models") or ())==51 else providers)
    def encode(value):
        return value.isoformat() if hasattr(value,'isoformat') else str(value)
    (tmp_path/'anchor.json').write_bytes(request.openmeteo_raw_payload_bytes)
    (tmp_path/'precision.json').write_text(json.dumps(asdict(request.openmeteo_precision_guard.metadata),default=encode))
    fields=('city','city_id','city_timezone','temperature_metric','baseline_source_run_id','baseline_data_version',
            'baseline_source_available_at','openmeteo_source_run_id','openmeteo_source_available_at','source_cycle_time',
            'computed_at','expires_at','target_date','day0_observed_extreme_c','day0_observed_extreme_source',
            'day0_observed_extreme_observation_time')
    payload={key:getattr(request,key) for key in fields}
    payload.update(openmeteo_payload_json='anchor.json',precision_metadata_json='precision.json',bins=[asdict(b) for b in request.bins])
    request_path=tmp_path/'request.json';request_path.write_text(json.dumps(payload,default=encode))
    wake_path=tmp_path/'wake.json'
    publish=reactor_wake.publish_reactor_wake
    def notify_after_ready(**kwargs):
        assert not conn.in_transaction
        assert any('"stage": "POSTERIOR_READY"' in record.getMessage() for record in caplog.records)
        return publish(**kwargs,path=wake_path)
    monkeypatch.setattr(reactor_wake,'publish_reactor_wake',notify_after_ready)
    monkeypatch.setattr('src.state.db.get_world_connection_read_only',
        lambda: sqlite3.connect(f'file:{world_path}?mode=ro',uri=True))
    offered=delivery.scope_capture_offers_larger_provider_set(conn,city='Shanghai',target_date='2026-06-07',
        metric='high',decision_time=now,changed_sources=('day0_current_temperature_state',))
    assert offered['is_upgrade'],offered
    assert offered['changed_input_revisions']['day0_current_temperature_state']==input_identity
    conn.commit()  # Fixture source runs must be visible before CLI index/bootstrap.
    started=time.monotonic_ns()
    with reactor_wake.reactor_wake_listener_socket(path=wake_path) as listener:
        assert listener is not None
        listener.settimeout(2)
        code,response=cli._materialize(request_path,commit=True,init_schema=False,conn=conn,
            schema_ready=True,writer_lock=nullcontext)
        assert code==0,response
        materialized=time.monotonic_ns()
        assert listener.recv(1)==b'\x01'
        wake_received_at_ms=time.time_ns()//1_000_000
        wake=reactor_wake.read_reactor_wake(path=wake_path)
        assert wake is not None and wake.reason=='forecast_posterior_advanced'
    assert wake.forecast_families == (('Shanghai','2026-06-07','high'),)
    # Exercise the production held-family selector against a separate read-only
    # TRADE connection; an ended local date does not erase money at risk.
    monkeypatch.setattr('src.state.db.get_trade_connection_read_only',
        lambda: sqlite3.connect(f'file:{trade_path}?mode=ro',uri=True))
    assert main._forecast_wake_held_families(wake.forecast_families)==frozenset(wake.forecast_families)
    row=conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",(response['posterior_id'],)).fetchone()
    provenance=json.loads(row['provenance_json'])
    assert provenance['day0_current_temperature_state']==input_identity
    consumed=delivery.scope_capture_offers_larger_provider_set(conn,city='Shanghai',target_date='2026-06-07',
        metric='high',decision_time=now,changed_sources=('day0_current_temperature_state',))
    assert not consumed['is_upgrade'],consumed
    from src.data.replacement_forecast_readiness import ReplacementForecastReadinessDecision
    cert=conn.execute("SELECT * FROM readiness_state WHERE readiness_id=?",(response['readiness_id'],)).fetchone()
    ready=ReplacementForecastReadinessDecision(
        readiness_id=cert["readiness_id"],status=cert["status"],
        reason_codes=tuple(json.loads(cert["reason_codes_json"])),
        dependency_json=json.loads(cert["dependency_json"]),
        provenance_json=json.loads(cert["provenance_json"]),
        expires_at=datetime.fromisoformat(cert["expires_at"]))
    read=read_replacement_forecast_bundle(conn,
        baseline_bundle=reader_fixtures._BaselineBundle(reader_fixtures._Evidence("b0-run")),
        readiness=ready,city="Shanghai",target_date="2026-06-07",temperature_metric="high",
        decision_time=now,current_bin_topology_hash=row["bin_topology_hash"])
    assert read.ok, read
    assert read.bundle.q == pytest.approx(json.loads(row["q_json"]))
    assert abs(sum(read.bundle.q.values())-1)<1e-9
    q_served_monotonic=time.monotonic_ns()

    cancel_ms=(_cancel_prior_rest(trade,conn,bundle=read.bundle,at=now)
               if reaction_path=='replace' else None)
    auction,auction_ms=_auction_from_served_observation(read.bundle,at=now,trade=trade)
    print('MEASURED_GLOBAL_AUCTION',json.dumps({'auction_ms':auction_ms,
        'candidate_count':auction.decision.candidate_input_count,
        'selected_action':'BUY',
        'selected_token':auction.decision.candidate.token_id,
        'selected_shares':str(auction.decision.shares)}))

    # ENTRY consumes the actual global-auction winner through the executor and
    # durable certificate/receipt closure. SELL remains a separate reduce-only
    # boundary test, not the auction's choice. External venue and forecast
    # acquisition/parent preparation are controlled harness inputs.
    submitted=[]
    class FakeVenue:
        def bind_submission_envelope(self,envelope): self.envelope=envelope
        def bind_signed_submission_identity_persister(self,persister): self.persister=persister
        def get_collateral_payload(self): return exit_fixtures._fresh_exit_collateral_payload()
        def place_limit_order(self,**kwargs):
            cmd=trade.execute('SELECT command_id,state,q_version FROM venue_commands ORDER BY created_at DESC LIMIT 1').fetchone()
            assert cmd is not None and cmd['state']=='SUBMITTING'
            assert cmd['q_version']==read.bundle.posterior_identity_hash
            submitted.append(kwargs)
            return exit_fixtures._fake_submit_result(self.envelope,order_id='fixture-venue-order')
    monkeypatch.setattr('src.data.polymarket_client.PolymarketClient',FakeVenue)
    try:
        if reaction_path in {'entry','replace'}:
            order,journal=_submit_selected_entry(trade,world,auction,read.bundle,monkeypatch)
        else:
            order=execute_exit_order(create_exit_order_intent(
            trade_id='source-reaction-held',token_id=exit_fixtures.YES_TOKEN,shares=5,
            current_price=0.75,best_bid=0.74,exact_limit_price=0.75,submit_order_type='GTC',
            executable_snapshot_id=snapshot_id,
            executable_snapshot_hash=exit_fixtures._snapshot_hash(trade,snapshot_id),
            executable_snapshot_min_tick_size=Decimal('0.01'),executable_snapshot_min_order_size=Decimal('0.01'),
            executable_snapshot_neg_risk=False),conn=trade,decision_id='source-reaction-decision',
                q_version=read.bundle.posterior_identity_hash)
            assert submitted,order
        trade.commit()
        command=get_command(trade,order.command_id)
        assert command['state']=='ACKED',order
        journal=list_events(trade,order.command_id)
        assert any(event['event_type']=='SUBMIT_ACKED' for event in journal)
    finally:
        exit_fixtures._clear_exit_submit_prereqs()

    events=[]
    for record in caplog.records:
        message=record.getMessage()
        prefix="OBSERVATION_REACTION_TRACE "
        if message.startswith(prefix):
            events.append(json.loads(message[len(prefix):]))
    trace=completed_trace(events,posterior_identity_hash=row["posterior_identity_hash"])
    assert trace["status"]=="OBSERVED_COMPLETE",trace
    assert isinstance(trace["q_served_at_ms"],int)
    assert isinstance(trace["venue_ack_at_ms"],int)
    assert trace["q_served_at_ms"]>=trace["posterior_ready_at_ms"]>=trace["world_committed_at_ms"]
    assert trace["venue_ack_at_ms"]>=trace["q_served_at_ms"]

    assert trace['command_id']==order.command_id
    assert any(event['event_id']==trace['event_id'] for event in journal)
    measured={"posterior_id":response['posterior_id'],
        "cancel_confirm_and_replay_ms":cancel_ms,
        "reaction_path":reaction_path,"auction_ms":auction_ms,
        "receipt_to_world_ms":world_committed_at_ms-response_received_at_ms,
        "world_to_posterior_ms":trace["posterior_ready_at_ms"]-world_committed_at_ms,
        "materialize_ms":(materialized-started)/1e6,
        "incumbent_without_carrier":incumbent_without_carrier,
        "posterior_to_wake_ms":wake_received_at_ms-trace['posterior_ready_at_ms'],
        "wake_to_q_ms":trace['q_served_at_ms']-wake_received_at_ms,
        "posterior_to_q_ms":trace["q_served_at_ms"]-trace["posterior_ready_at_ms"],
        "serve_ms":(q_served_monotonic-materialized)/1e6,
        "q_to_ack_ms":trace["venue_ack_at_ms"]-trace["q_served_at_ms"],
        "receipt_to_ack_ms":trace["receipt_to_ack_ms"],"q":dict(read.bundle.q)}
    print("MEASURED_HARNESS",json.dumps(measured,sort_keys=True))
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='observation_prints'").fetchone()[0]==0
    assert world.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='forecast_posteriors'").fetchone()[0]==0
    conn.close();world.close();trade.close()
