# Created: 2026-09-29
# Last reused/audited: 2026-10-05
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
    completed_trace, emit_observation_committed, emit_posterior_ready, emit_stage, emit_venue_ack,
    emit_wake_received,
)

_hko_native_surfaces = fixtures._hko_native_surfaces
_hko_source_surface = fixtures._hko_source_surface
_historical_shanghai_component_surface = fixtures._historical_shanghai_component_surface
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


def _cancel_prior_rest(trade, forecasts, world, *, bundle, at):
    """Real C3 valuation/batch cancellation; denied budget cannot authorize replacement."""
    from src.execution.staleness_cancel import run_c3_staleness_cancel_cycle
    family=(bundle.city,bundle.target_date,bundle.temperature_metric)
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
    denied=run_c3_staleness_cancel_cycle(trade,trade,forecasts,venue,world_conn_ro=world,clock=lambda: at,rate_budget=budget)
    assert denied['cancel_set_size']==1,denied
    # This harness has no complete current global scope for the prior rest's
    # family, so C3 cancels it protectively (unavailable authority), never on
    # q_version inequality: a posterior identity change alone is not a cancel.
    [prior]=denied['valuations']
    assert prior.action=='CANCEL' and prior.evidence['authority_valid'] is False,prior
    assert prior.reason.startswith('ENTRY_REST_CURRENT_SCOPE_UNAVAILABLE'),prior
    assert not denied['confirmed_families'] and not venue.calls
    assert trade.execute('SELECT COUNT(*) FROM venue_commands').fetchone()[0]==1
    budget.allowed=True
    confirmed=run_c3_staleness_cancel_cycle(trade,trade,forecasts,venue,world_conn_ro=world,clock=lambda: at,rate_budget=budget)
    assert confirmed['confirmed_families']=={family},confirmed
    assert trade.execute("SELECT state FROM venue_commands WHERE command_id='prior-rest'").fetchone()[0]=='CANCELLED'
    trade.commit()
    assert venue.calls==[['prior-venue']]
    replay=run_c3_staleness_cancel_cycle(trade,trade,forecasts,venue,world_conn_ro=world,clock=lambda: at,rate_budget=budget)
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
def test_observation_revision_materializes_then_serves(monkeypatch,caplog,tmp_path,trade_schema,_historical_shanghai_component_surface,incumbent_without_carrier,reaction_path):
    import scripts.materialize_replacement_forecast_live as cli
    import src.main as main  # Already resident in a warm trading process.
    from src.data import replacement_fusion_upgrade_trigger as delivery
    from src.runtime import reactor_wake
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.venue_command_repo import get_command, list_events
    from src.state.schema.observation_prints_schema import append_print, ensure_table

    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    world_path, trade_path = (tmp_path/name for name in ('zeus-world.db','zeus_trades.db'))
    # Normal owned ground/anchor/provider proof for the same June-07 scenario the
    # sibling Shanghai materializer tests use, never the legacy unproven seam.
    conn,basis=fixtures._historical_shanghai_component_request(tmp_path,monkeypatch,computed_at=fixtures._dt(8))
    trade=sqlite3.connect(trade_path);trade.row_factory=sqlite3.Row
    trade_schema.backup(trade)
    world=sqlite3.connect(world_path)
    world.execute('PRAGMA journal_mode=WAL')
    ensure_table(world)
    from src.state.ledger import apply_architecture_kernel_schema
    apply_architecture_kernel_schema(world);world.commit()
    monkeypatch.setattr('src.state.db.get_world_connection',
        lambda *a,**kw: sqlite3.connect(world_path))
    # control_plane can be imported before this test in the full suite; bind
    # its imported factory to the same isolated DB, without bypassing its query.
    monkeypatch.setattr('src.control.control_plane.get_world_connection',
        lambda *a,**kw: sqlite3.connect(world_path))
    # Production-like ownership: writes use distinct WORLD/FORECAST/TRADE files.
    # Only WORLD is attached read-only while preparing a posterior.
    conn.execute('ATTACH DATABASE ? AS world',(f'file:{world_path}?mode=ro',))
    exit_fixtures._enable_exit_submit_prereqs(trade,monkeypatch)
    snapshot_id=exit_fixtures._ensure_snapshot(trade,snapshot_id='source-reaction-book',
        orderbook_top_bid='0.74',orderbook_top_ask='0.75')
    trade.execute("INSERT INTO position_current(position_id,phase,market_id,city,target_date,temperature_metric,chain_state,chain_shares,chain_cost_basis_usd,updated_at) VALUES('source-reaction-held','active','condition-test','Shanghai','2026-06-07','high','synced',5,2,'2026-06-06T18:00:00Z')")
    trade.commit()
    # End fixture preparation before the independent WORLD evidence writer.
    conn.commit()
    if incumbent_without_carrier:
        incumbent=materialize_replacement_forecast_live(conn,basis)
        assert incumbent.ok,incumbent
        conn.commit()
        prior=conn.execute('SELECT provenance_json FROM forecast_posteriors WHERE posterior_id=?',(incumbent.posterior_id,)).fetchone()
        assert not json.loads(prior[0]).get('day0_current_temperature_state')
    now=fixtures._dt(18,10)
    request=fixtures._refresh_shanghai_owner_request(conn,monkeypatch,replace(basis,computed_at=now,
        expires_at=datetime(2026,6,7,2,tzinfo=timezone.utc),
        day0_observed_extreme_c=31.0,day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time=fixtures._dt(18,5).isoformat()),record_observed_prints=False)
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
    # The production per-row hook, over the exact committed revision.
    world.row_factory=sqlite3.Row
    committed_row=dict(world.execute("SELECT * FROM observation_prints WHERE raw_report LIKE 'METAR ZSPD%'").fetchone())
    world.row_factory=None
    emit_observation_committed(committed_row,world_committed_at_ms=world_committed_at_ms,
                               disposition="ADVANCES_SOURCE_FRONTIER")
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
            'day0_observed_extreme_observation_time','day0_observed_extreme_sample_count','day0_observed_extreme_unit')
    payload={key:getattr(request,key) for key in fields}
    payload['openmeteo_anchor_artifact_id']=request.anchor_artifact_id
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
        emit_wake_received(wake_id=wake.wake_id)  # The main.py consumer hook, wake_id only.
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
    from src.data import replacement_forecast_bundle_reader as bundle_reader
    class ReaderClock(datetime):
        # The reader judges source-run coverage expiry against its own wall clock;
        # pin it to the scenario's decision cut, never to the day the suite runs.
        @classmethod
        def now(cls,tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(bundle_reader,'datetime',ReaderClock)
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
        decision_time=now.isoformat(),current_bin_topology_hash=row["bin_topology_hash"])
    assert read.ok, read
    assert read.bundle.q == pytest.approx(json.loads(row["q_json"]))
    assert abs(sum(read.bundle.q.values())-1)<1e-9
    q_served_monotonic=time.monotonic_ns()

    cancel_ms=(_cancel_prior_rest(trade,conn,world,bundle=read.bundle,at=now)
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
    (ready,)=[e for e in events if e["stage"]=="POSTERIOR_READY"]
    assert ready["input_reference_status"]=="CONSUMED_LEDGER_REVISION"
    assert ready["observation_ref"]["id"]==committed_row["id"]
    assert [(e["wake_id"],e["posterior_identity_hash"]) for e in events if e["stage"]=="WAKE_PUBLISHED"]==[
        (wake.wake_id,row["posterior_identity_hash"])]
    assert trace["all_hops_observed"] and trace["lineage_grade"]=="EXPLICIT_REVISION",trace
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


# ---------------------------------------------------------------------------
# Production stage hooks: each emits once with exact ids and never raises.
# ---------------------------------------------------------------------------

def _trace_events(caplog):
    prefix="OBSERVATION_REACTION_TRACE "
    return [json.loads(r.getMessage()[len(prefix):]) for r in caplog.records
            if r.getMessage().startswith(prefix)]


def test_source_tick_emits_one_source_commit_per_inserted_row(monkeypatch,tmp_path,caplog):
    import threading
    from src.config import cities_by_name
    from src.data import station_temperature_adapters as adapters
    from src.data import replacement_forecast_production as production
    from src.data.physical_current_sources import load_physical_current_sources
    from src.runtime.observation_reaction_trace import observation_revision_reference
    from src.state import db, write_coordinator as coordinator
    from src.state.schema.observation_prints_schema import append_print, ensure_table
    import src.ingest_main as ingest
    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    city=cities_by_name["Ankara"]
    route=next(r for r in load_physical_current_sources()[0] if r.provider=="mgm_metar" and r.station_id==city.wu_station)
    now=datetime.now(timezone.utc)
    newest,older=now-timedelta(minutes=2),now-timedelta(minutes=40)
    path=tmp_path/"world.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_table(conn)
        # Already-ledgered sample: suppressed by append_print, so no stage line.
        append_print(conn,city=city.name,station_id=route.station_id,source_channel=route.source_channel,
            publish_ts_utc=(now-timedelta(minutes=20)).isoformat(),value_native=12.0,unit=route.unit,
            fetched_at_utc=(now-timedelta(minutes=19)).isoformat(),raw_report="seed")
    samples=(adapters._sample(route,newest,13.0,now,"a"*64),adapters._sample(route,older,11.0,now,"b"*64),
             adapters._sample(route,now-timedelta(minutes=20),12.0,now,"c"*64))
    class Lease:
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def record_commit(self,**kw):pass
    monkeypatch.setattr(adapters,"fetch_station_temperature",lambda *a,**kw:samples)
    monkeypatch.setattr(db,"world_write_mutex",lambda:threading.Lock())
    monkeypatch.setattr(db,"get_world_connection",lambda **kw:sqlite3.connect(path))
    monkeypatch.setattr(coordinator,"default_runtime_write_coordinator",lambda:SimpleNamespace(lease=lambda *a,**kw:Lease()))
    monkeypatch.setattr(production,"_replacement_forecast_live_materialization_queue_config",lambda:{})
    monkeypatch.setattr("src.data.physical_current_delivery.current_temperature_priority_families",lambda:{})
    monkeypatch.setattr(production,"_enqueue_fusion_upgrade_reseeds_if_needed",lambda cfg,**kw:{"status":"FUSION_UPGRADE_TRIGGER"})
    monkeypatch.setattr(ingest,"_physical_current_pending_wakes",set())
    before=time.time_ns()//1_000_000
    result=ingest._day0_current_temperature_source_tick(city,route)
    assert result["status"]=="COMMITTED" and result["inserted"]==2
    commits=[e for e in _trace_events(caplog) if e["stage"]=="SOURCE_COMMITTED"]
    with sqlite3.connect(path) as conn:
        conn.row_factory=sqlite3.Row
        rows={r["publish_ts_utc"]:dict(r) for r in conn.execute("SELECT * FROM observation_prints WHERE raw_report!='seed'")}
    assert len(commits)==2
    by_clock={e["observation_ref"]["publish_ts_utc"]:e for e in commits}
    for clock,row in rows.items():
        event=by_clock[datetime.fromisoformat(clock).astimezone(timezone.utc).isoformat()]
        # The full immutable revision, rebuilt from the committed row itself.
        assert event["observation_ref"]==observation_revision_reference(row)
        assert event["input_reference_status"]=="EXPLICIT_LEDGER_REVISION"
    assert by_clock[newest.isoformat()]["commit_disposition"]=="ADVANCES_SOURCE_FRONTIER"
    assert by_clock[older.isoformat()]["commit_disposition"]=="BEHIND_SOURCE_FRONTIER"
    clocks={e["world_committed_at_ms"] for e in commits}
    assert len(clocks)==1 and before<=next(iter(clocks))<=min(e["recorded_at_ms"] for e in commits)


def test_source_commit_hook_never_raises_and_skips_invalid_rows(caplog):
    from src.runtime.observation_reaction_trace import emit_observation_committed
    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    emit_observation_committed({"id":0},world_committed_at_ms=1)
    emit_observation_committed({},world_committed_at_ms=1)
    assert _trace_events(caplog)==[]


def _posterior_db(tmp_path,*,provenance,prints=()):
    conn=sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE forecast_posteriors(posterior_id INTEGER PRIMARY KEY,city TEXT,target_date TEXT,"
                 "temperature_metric TEXT,posterior_identity_hash TEXT,provenance_json TEXT,computed_at TEXT)")
    conn.execute("CREATE TABLE readiness_state(readiness_id TEXT PRIMARY KEY,computed_at TEXT,dependency_json TEXT,"
                 "city TEXT,target_local_date TEXT,temperature_metric TEXT,status TEXT)")
    conn.execute("INSERT INTO forecast_posteriors VALUES(7,'Tokyo','2026-10-05','high','hash-7',?,'2026-10-05T08:10:00+00:00')",
                 (json.dumps(provenance),))
    conn.execute("INSERT INTO readiness_state VALUES('ready-7','2026-10-05T08:10:00+00:00',?,'Tokyo','2026-10-05','high','READY')",
                 (json.dumps({"dependencies":[{"role":"soft_anchor_posterior","posterior_id":7}]}),))
    tmp_path.mkdir(parents=True,exist_ok=True)
    conn.execute("ATTACH DATABASE ? AS world",(str(tmp_path/"world.db"),))
    conn.execute("CREATE TABLE world.observation_prints(id INTEGER PRIMARY KEY,city TEXT,station_id TEXT,source_channel TEXT,"
                 "publish_ts_utc TEXT,value_native REAL,unit TEXT,fetched_at_utc TEXT,raw_report TEXT)")
    conn.executemany("INSERT INTO world.observation_prints VALUES(?,?,?,?,?,?,?,?,?)",prints)
    return conn


_PRINT_A=(1,'Tokyo','RJTT','x','2026-10-05T08:00:00+00:00',20.0,'C','2026-10-05T08:01:00+00:00','a')
_PRINT_B=(2,'Tokyo','RJTT','x','2026-10-05T08:00:00+00:00',21.0,'C','2026-10-05T08:02:00+00:00','b')
_PRINT_A2=(3,'Tokyo','RJTT','x','2026-10-05T08:00:00+00:00',20.0,'C','2026-10-05T08:03:00+00:00','a2')
_STATE={"source":"x","observed_at_utc":"2026-10-05T08:00:00+00:00","value_native":20.0}


def test_posterior_ready_carries_the_consumed_row_through_a_b_a(tmp_path,caplog):
    from src.runtime.observation_reaction_trace import emit_posterior_ready, observation_revision_reference
    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    names=('id','city','station_id','source_channel','publish_ts_utc','value_native','unit','fetched_at_utc','raw_report')
    for consumed in (_PRINT_A,_PRINT_A2):
        caplog.clear()
        conn=_posterior_db(tmp_path/str(consumed[0]),prints=(_PRINT_A,_PRINT_B,_PRINT_A2),provenance={
            "day0_current_temperature_state":_STATE,
            "day0_current_temperature_input_ref":{"print_id":consumed[0]}})
        assert emit_posterior_ready(conn,7,wake_published=False,readiness_id="ready-7")=="hash-7"
        (ready,)=_trace_events(caplog)
        assert ready["stage"]=="POSTERIOR_READY" and ready["posterior_identity_hash"]=="hash-7"
        assert ready["input_reference_status"]=="CONSUMED_LEDGER_REVISION"
        assert ready["observation_ref"]==observation_revision_reference(dict(zip(names,consumed)))
        assert ready["readiness_id"]=="ready-7"
        conn.close()


def test_posterior_ready_without_carried_row_leaves_a_b_a_unresolved(tmp_path,caplog):
    from src.runtime.observation_reaction_trace import emit_posterior_ready
    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    conn=_posterior_db(tmp_path,prints=(_PRINT_A,_PRINT_B,_PRINT_A2),
                       provenance={"day0_current_temperature_state":_STATE})
    assert emit_posterior_ready(conn,7,wake_published=False)=="hash-7"
    (ready,)=_trace_events(caplog)
    assert ready["observation_ref"] is None and ready["input_reference_status"]=="AMBIGUOUS_INPUT_REVISION"
    conn.close()


def test_posterior_ready_kma_event_reference_is_named_not_guessed(tmp_path,caplog):
    from src.runtime.observation_reaction_trace import emit_posterior_ready
    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    carried={"kma_event_id":"kma-1"}
    conn=_posterior_db(tmp_path,provenance={"day0_current_temperature_state":_STATE,
                                            "day0_current_temperature_input_ref":carried})
    emit_posterior_ready(conn,7,wake_published=False)
    (ready,)=_trace_events(caplog)
    assert ready["input_ref"]==carried and ready["observation_ref"] is None
    assert ready["input_reference_status"]=="CONSUMED_KMA_EVENT_REVISION"
    conn.close()


def test_posterior_ready_never_raises_into_the_materializer(caplog):
    from src.runtime.observation_reaction_trace import emit_posterior_ready
    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    conn=sqlite3.connect(":memory:")
    assert emit_posterior_ready(conn,7,wake_published=False) is None
    conn.close()
    assert emit_posterior_ready(conn,7,wake_published=False) is None
    assert _trace_events(caplog)==[]


def test_reader_records_the_exact_row_without_changing_state_identity(tmp_path):
    from src.config import cities_by_name
    from src.data.day0_hourly_vectors import Day0CurrentTemperatureState, read_day0_current_temperature_state
    from src.data.physical_current_sources import load_physical_current_sources
    from src.data import station_temperature_adapters as adapters
    from src.state.schema.observation_prints_schema import append_print, ensure_table
    city=cities_by_name["Ankara"]
    route=next(r for r in load_physical_current_sources()[0] if r.provider=="mgm_metar" and r.station_id==city.wu_station)
    now=datetime.now(timezone.utc);observed=now-timedelta(minutes=2)
    sample=adapters._sample(route,observed,12.0,now,"a"*64)
    conn=sqlite3.connect(tmp_path/"world.sqlite");ensure_table(conn)
    assert append_print(conn,city=city.name,station_id=route.station_id,source_channel=route.source_channel,
        publish_ts_utc=observed.isoformat(),value_native=12.0,unit=route.unit,
        fetched_at_utc=now.isoformat(),raw_report=sample.raw_report)
    conn.commit()
    (row_id,)=conn.execute("SELECT id FROM observation_prints").fetchone()
    from zoneinfo import ZoneInfo
    state=read_day0_current_temperature_state(conn=conn,city=city,
        target_date=observed.astimezone(ZoneInfo(city.timezone)).date().isoformat(),decision_time=now)
    assert state is not None and state.input_ref=={"print_id":row_id}
    # Telemetry-only field: identity and equality stay exactly the old content law.
    assert set(state.identity())=={"value_native","observed_at_utc","source"}
    assert state==Day0CurrentTemperatureState(value_native=state.value_native,observed_at=state.observed_at,
        source=state.source,clock_evidence=state.clock_evidence)
    conn.close()


def test_wake_published_names_its_own_posterior_and_hooks_never_raise(caplog,monkeypatch):
    import scripts.materialize_replacement_forecast_live as cli
    from src.runtime import reactor_wake
    from src.runtime import observation_reaction_trace as trace
    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    monkeypatch.setattr(reactor_wake,"publish_reactor_wake",lambda **kw:SimpleNamespace(wake_id="wake-9"))
    request=fixtures._request()
    assert cli._publish_materialization_wake(request,posterior_identity_hash="hash-7") is True
    assert [(e["stage"],e["wake_id"],e["posterior_identity_hash"]) for e in _trace_events(caplog)]==[
        ("WAKE_PUBLISHED","wake-9","hash-7")]
    caplog.clear()
    # A missing posterior identity never labels the wake with anything else.
    assert cli._publish_materialization_wake(request) is True
    assert _trace_events(caplog)==[]
    monkeypatch.setattr(trace._LOG,"info",lambda *a,**k:(_ for _ in ()).throw(RuntimeError("sink")))
    trace.emit_wake_published(wake_id="w",posterior_identity_hash="h")
    trace.emit_wake_received(wake_id="w-raise")


def test_wake_received_once_per_wake_and_joined_only_through_its_publication(caplog,monkeypatch):
    from src.runtime import observation_reaction_trace as trace
    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    monkeypatch.setattr(trace,"_RECEIVED",{})
    trace.emit_wake_received(wake_id="wake-9")
    trace.emit_wake_received(wake_id="wake-9")
    trace.emit_wake_received(wake_id="")
    received=_trace_events(caplog)
    assert [(e["stage"],e["wake_id"]) for e in received]==[("WAKE_RECEIVED","wake-9")]
    assert "posterior_identity_hash" not in received[0] or received[0]["posterior_identity_hash"] is None
    published=[{"stage":"WAKE_PUBLISHED","wake_id":"wake-9","posterior_identity_hash":"hash-7"}]
    labels=trace.published_wake_hashes(published)
    assert trace.wake_posterior_hash(received[0],labels)=="hash-7"
    # A second, different publication label for one wake_id is ambiguity, not latest-wins.
    assert trace.published_wake_hashes(published+[dict(published[0],posterior_identity_hash="hash-8")])=={}


def test_main_consumer_records_wake_receipt_before_dispatch(monkeypatch,caplog):
    import src.main as main_module
    from src.runtime import reactor_wake as wake_module
    from src.runtime import observation_reaction_trace as trace
    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    monkeypatch.setattr(trace,"_RECEIVED",{})
    wake=wake_module.ReactorWake("wake-consumed","2026-10-05T08:00:00+00:00","replacement_forecast_materializer",
        "forecast_posterior_advanced",forecast_families=(("Tokyo","2026-10-05","high"),))
    seen=[]
    def reactor(**kwargs):
        seen.append([e["wake_id"] for e in _trace_events(caplog) if e["stage"]=="WAKE_RECEIVED"])
        return False
    class IdleLock:
        def locked(self):return False
    monkeypatch.setattr(main_module,"_defer_for_held_position_monitor",lambda _job:False)
    monkeypatch.setattr(main_module,"_exit_monitor_excluded_wake_ids",lambda:frozenset())
    monkeypatch.setattr(main_module,"_forecast_wake_held_families",lambda _f:frozenset())
    monkeypatch.setattr(wake_module,"exact_held_sell_completion_wake_ids",lambda **_k:frozenset())
    monkeypatch.setattr(wake_module,"strict_generic_held_family_completion_wakes",lambda **_k:())
    monkeypatch.setattr(wake_module,"read_reactor_wake",lambda **_k:wake)
    monkeypatch.setattr(wake_module,"coalescible_reactor_wakes",lambda _w:(wake,))
    monkeypatch.setattr(main_module,"_edli_reactor_active_lock",IdleLock())
    monkeypatch.setattr(main_module,"_edli_event_reactor_cycle",reactor)
    monkeypatch.setattr(main_module,"_edli_last_reactor_wake_id",None)
    assert main_module._edli_reactor_wake_poll_once() is False
    assert main_module._edli_reactor_wake_poll_once() is False
    assert seen==[["wake-consumed"],["wake-consumed"]]
    assert [e["wake_id"] for e in _trace_events(caplog) if e["stage"]=="WAKE_RECEIVED"]==["wake-consumed"]
