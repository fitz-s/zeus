# Created: 2026-10-08
# Last reused/audited: 2026-10-09
# Authority basis: offline scheduled statistical exit and normal ENTRY lineage.
"""No injected posterior, decision, certificate, command, or fill projection."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import os
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import httpx
import pytest
from tests.engine import test_scheduled_physical_exit_replay as source
from tests.test_replacement_forecast_materializer import _hko_source_surface, _hko_native_surfaces  # noqa: F401
from tests.fakes.scheduled_exit_venue import ScheduledExitVenue
from src.data.station_temperature_adapters import _bounded_body as native_bounded_body
UTC = timezone.utc
HTTP_CLIENT = httpx.Client


class StatisticalScheduledPump(source.ScheduledPump):
    """Keep the capital sidecar's declared SQLite retry on its native cadence."""

    def __init__(self, monkeypatch, clock):
        super().__init__(monkeypatch, clock)
        self.collateral_write_deferrals = []

    def advance(self, seconds=0):
        from apscheduler.schedulers.base import STATE_RUNNING, STATE_PAUSED
        from src.state.write_coordinator import WriteLeaseTimeout

        self.clock[0] += timedelta(seconds=seconds)
        offset = len(self.events)
        self.scheduler.state = STATE_RUNNING
        try:
            self.scheduler._process_jobs()
        finally:
            self.scheduler.state = STATE_PAUSED
        fired = self.events[offset:]
        for event in fired:
            error = getattr(event, 'exception', None)
            if error is None:
                continue
            # The production wrapper records FAILED and APScheduler retries
            # next tick. Match only its actual immediate SQLite BEGIN refusal;
            # every other scheduler error still fails this replay immediately.
            assert (
                event.job_id == 'collateral_snapshot_refresh'
                and isinstance(error, WriteLeaseTimeout)
                and str(error) == 'SQLite write deferred at BEGIN for '
                    'owner=collateral_snapshot_persist sqlite_errorname=SQLITE_BUSY'
                and isinstance(error.__cause__, sqlite3.OperationalError)
                and getattr(error.__cause__, 'sqlite_errorcode', None) == sqlite3.SQLITE_BUSY
            ), fired
            self.collateral_write_deferrals.append({
                'job_id': event.job_id,
                'scheduled_run_time': event.scheduled_run_time.isoformat(),
                'observed_at': self.clock[0].isoformat(),
                'exception_type': type(error).__name__,
                'exception': str(error),
                'sqlite_errorname': error.__cause__.sqlite_errorname,
            })
        return fired


@pytest.mark.parametrize('mismatch',[
    'job','type','message','missing_cause','non_sqlite_cause','sqlite_code','unrelated_error',
])
def test_statistical_scheduler_rejects_other_job_errors(tmp_path,monkeypatch,mismatch):
    """A capital retry exception cannot conceal another scheduler failure."""
    from apscheduler.executors.debug import DebugExecutor
    from src.state.write_coordinator import WriteLeaseTimeout

    path=tmp_path/'contention.db'
    holder=sqlite3.connect(path)
    contender=sqlite3.connect(path,timeout=0)
    try:
        holder.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError) as busy:
            contender.execute('BEGIN IMMEDIATE')
        holder.rollback()
        with pytest.raises(sqlite3.OperationalError) as invalid:
            contender.execute('SELECT * FROM table_that_does_not_exist')
    finally:
        contender.close()
        holder.close()
    cause=busy.value
    message=('SQLite write deferred at BEGIN for '
        'owner=collateral_snapshot_persist sqlite_errorname=SQLITE_BUSY')
    error_type=WriteLeaseTimeout
    job_id='collateral_snapshot_refresh'
    if mismatch=='job':job_id='another_job'
    elif mismatch=='type':error_type=ValueError
    elif mismatch=='message':message='SQLite write deferred at COMMIT'
    elif mismatch=='missing_cause':cause=None
    elif mismatch=='non_sqlite_cause':cause=RuntimeError('database is locked')
    elif mismatch=='sqlite_code':cause=invalid.value
    elif mismatch=='unrelated_error':
        error_type=RuntimeError
        message='unrelated scheduler failure'
        cause=None
    error=error_type(message)
    def fail():
        raise error from cause
    clock=[datetime(2026,10,1,18,tzinfo=UTC)]
    pump=StatisticalScheduledPump(monkeypatch,clock)
    try:
        # The complete replay obtains this synchronous executor from its main
        # monitor registration; this isolated error-contract test has no main.
        pump.scheduler.remove_executor('default',shutdown=True)
        pump.scheduler.add_executor(DebugExecutor(),alias='default')
        pump.scheduler.add_job(fail,'date',run_date=clock[0],id=job_id)
        with pytest.raises(AssertionError):
            pump.advance()
        assert pump.events[-1].exception is error
        assert pump.collateral_write_deferrals==[]
    finally:
        pump.close()


def _empty_market_owner(conn, request, tmp_path, monkeypatch, *, direction, held_bin, **kwargs):
    """External market identities only. The canonical trading ledger is empty."""
    from src.state import db
    request.target_date += timedelta(days=1)
    path = tmp_path / 'zeus_trades.db'
    monkeypatch.setattr(db, '_zeus_trade_db_path', lambda: path)
    trade = sqlite3.connect(path)
    trade.row_factory = sqlite3.Row
    db.init_schema_trade_only(trade)
    for index, item in enumerate(request.bins):
        conn.execute('INSERT INTO market_events(market_slug,city,target_date,temperature_metric,condition_id,token_id,range_label,range_low,range_high,created_at,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            ('highest-temperature-in-shanghai-on-october-2-2026', request.city, str(request.target_date), request.temperature_metric,
             '0x'+f'{index+1:064x}', str(1000+index*2), item.bin_id, item.lower_c, item.upper_c,
             request.computed_at.isoformat(), request.computed_at.isoformat()))
    conn.commit()
    trade.commit()
    trade.close()
    trade = db.get_trade_connection_with_world_required(write_class='live')
    scope = SimpleNamespace(condition_id='0x'+f'{held_bin+1:064x}', token_id=str(1000+held_bin*2),
        no_token_id=str(1001+held_bin*2), city=request.city, target_date=str(request.target_date),
        temperature_metric=request.temperature_metric, direction=direction, bin_label=request.bins[held_bin].bin_id)
    return trade, scope


@pytest.fixture
def scheduled_source(tmp_path, monkeypatch, request):
    from tests import test_replacement_forecast_materializer as inputs
    original = inputs._shanghai_current_owner_request
    monkeypatch.setattr(inputs, '_shanghai_current_owner_request', lambda *args, **kwargs:
        original(*args, computed_at=datetime(2026,10,1,15,tzinfo=UTC), **kwargs))
    monkeypatch.setattr(source, '_seed_market_and_holding', _empty_market_owner)
    monkeypatch.setattr(source, 'ScheduledPump', StatisticalScheduledPump)
    generator=source.scheduled_source.__wrapped__(tmp_path, monkeypatch, request)
    case=next(generator)
    cleanup=_manage_replay_threads(case,monkeypatch)
    try:
        yield case
    finally:
        fault=getattr(case,'capture_fault',None)
        if fault is not None:
            fault['blocked'].chmod(fault['restore_mode'])
        cleanup()
        generator.close()
        for clock_db in getattr(case,'queue_clock_databases',()):
            clock_db.close()


def _manage_replay_threads(case,monkeypatch):
    """Bound worker lifetime to this synthetic process, preserving each body."""
    import threading
    import time
    from src import main
    from src.execution import exit_lifecycle
    stopped=threading.Event()
    case.threads=[]
    monkeypatch.setattr(main,'_edli_boot_fill_bridge_recovery_complete',threading.Event())
    monkeypatch.setattr(main,'_edli_boot_fill_bridge_recovery_thread',None)
    class ProcessStopped(BaseException):pass
    class RuntimeThreads:
        def __getattr__(self,name):return getattr(threading,name)
        def Thread(self,*args,**kwargs):
            target=kwargs.get('target')
            if target is main._held_position_monitor_recovery_worker_main:
                def bounded_target(*args,**kwargs):
                    try:target(*args,**kwargs)
                    except ProcessStopped:pass
                kwargs['target']=bounded_target
            thread=threading.Thread(*args,**kwargs)
            case.threads.append(thread)
            return thread
    class RuntimeTime:
        def __getattr__(self,name):return getattr(time,name)
        def sleep(self,seconds):
            if stopped.wait(seconds):raise ProcessStopped()
    monkeypatch.setattr(main,'threading',RuntimeThreads())
    monkeypatch.setattr(exit_lifecycle,'threading',RuntimeThreads())
    monkeypatch.setattr(main,'time',RuntimeTime())
    def cleanup():
        stopped.set()
        for thread in case.threads:
            thread.join(15)
            assert not thread.is_alive(),thread.name
    return cleanup


class StatisticalVenue(ScheduledExitVenue):
    """Native fake transport for a full three-bin family; economics frozen."""
    def __init__(self, *, case):
        self.case = case
        self.tokens = {str(1000+i): '0x'+f'{i//2+1:064x}' for i in range(6)}
        super().__init__(trade_db_path=case.tmp_path/'zeus_trades.db', event_clock=lambda:case.clock[0],
            condition_id=case.position.condition_id, yes_token_id=case.position.token_id,
            no_token_id=case.position.no_token_id, held_token=case.position.no_token_id,
            held_shares=0, bid='.07', fill_size=100, liquidity=100, cash=case.params.get('cash',10), fee_rate_bps=500,
            window_end=case.clock[0]+timedelta(hours=3,seconds=150),
            restored_window=((case.clock[0]+timedelta(hours=3,seconds=300),
                case.clock[0]+timedelta(hours=3,seconds=600)) if case.params.get('reentry_control') else None),
            market_end=datetime.combine(case.request.target_date+timedelta(days=1), datetime.min.time(),
                tzinfo=ZoneInfo(case.city.timezone)).astimezone(UTC))
        self.sdk.post_order = self.post_order
        self.sdk.get_balance_allowance = self.get_balance_allowance
        # All six books existed before the first probability. Extend account
        # transport to that same declared universe, without changing its law.
        self.inventory = {token: Decimal(0) for token in self.tokens}
        self.entry_prices = {token: Decimal(0) for token in self.tokens}
        self.token_books = {token: {
            'bid': self.bid if token == self.held_token else Decimal('.50'),
            'ask': self.bid + Decimal('.01') if token == self.held_token else Decimal('.51'),
            'bid_depth': Decimal(100), 'ask_depth': Decimal(100),
            'window_end': self.window_end, 'restored_window': self.restored_window,
        } for token in self.tokens}

    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        from src.venue.polymarket_v2_adapter import _deterministic_v2_order_id, _signed_order_bytes
        import hashlib
        order_id = _deterministic_v2_order_id(self.sdk, order, chain_id=137, neg_risk=False)
        signed = _signed_order_bytes(order)
        signed_hash = hashlib.sha256(signed).hexdigest()
        with sqlite3.connect(self.trade_db_path.as_uri()+'?mode=ro', uri=True) as conn:
            commands = conn.execute('SELECT command_id,state,envelope_id,token_id,side FROM venue_commands WHERE venue_order_id=?',(order_id,)).fetchall()
            assert len(commands)==1, 'POST requires one durable command identity'
            command=commands[0]
            assert command and command[1]=='SUBMITTING'
            persisted = conn.execute('SELECT signed_order_blob,canonical_pre_sign_payload_hash,raw_request_hash FROM venue_submission_envelopes WHERE order_id=? AND signed_order_hash=?',
                (order_id,signed_hash)).fetchone()
            assert persisted and bytes(persisted[0])==signed
            original=conn.execute('SELECT canonical_pre_sign_payload_hash,raw_request_hash FROM venue_submission_envelopes WHERE envelope_id=?',(command[2],)).fetchone()
            assert original==persisted[1:]
        side = 'SELL' if int(order.side)==1 else 'BUY'
        size = Decimal(order.makerAmount if side=='SELL' else order.takerAmount)/1_000_000
        price = Decimal(order.takerAmount if side=='SELL' else order.makerAmount)/1_000_000/size
        assert Decimal('.05')<=price<=Decimal('.95')
        token=str(order.tokenId)
        self.check_token(token)
        assert command[3:]==(token,side), 'signed order must match its durable token and side'
        book=self.book(token)
        levels=book['asks' if side=='BUY' else 'bids']
        assert levels, 'the native executable side has no liquidity at POST'
        # The declared finite window removes bids only; asks are continuously
        # advertised by book(). A BUY must follow those same executable asks.
        if side=='SELL':
            assert self.window_open(token), 'executable bid window expired before POST'
        executable=Decimal(levels[0]['price'])
        assert Decimal('.05')<=executable<=Decimal('.95')
        if side=='BUY':
            fee=size*Decimal(self.fee_rate_bps)/10_000*price*(1-price)
            assert size*price+fee<=self.cash
            crossing=price>=executable
        else:
            assert size<=self.inventory[token]
            crossing=price<=executable
        assert not post_only or not crossing
        assert size<=Decimal(levels[0]['size'])
        assert crossing or str(order_type)=='GTC'
        matched_size=size if crossing else Decimal(0)
        cap=self.case.params.get('first_exit_match_cap')
        if cap is not None and crossing and side=='SELL' and token==self.held_token:
            previous=[post for post in self.posts if post['side']=='SELL' and post['token_id']==token]
            if not previous:
                # Declared external matching loss after the advertised book
                # capture. The normal action law still chooses its full size.
                assert str(order_type)=='FAK'
                matched_size=min(size,Decimal(str(cap)))
        self.orders[order_id]={'id':order_id,'orderID':order_id,'status':'MATCHED' if crossing else 'LIVE',
            'created_at':self.now().isoformat(),
            'market':self.tokens[token],'asset_id':token,'side':side,'price':str(price),
            'original_size':str(size),'size_matched':str(matched_size),'associate_trades':[]}
        fill_price=executable if crossing else price
        self.fill_prices[order_id]=fill_price
        self.record('post_order',order_id=order_id,command_id=command[0],signed_order_hash=signed_hash,
            token_id=token,condition_id=self.tokens[token],side=side,size=str(size),filled_size=str(matched_size),price=str(price),
            maker_amount_micro=str(order.makerAmount),taker_amount_micro=str(order.takerAmount),
            fill_price=str(fill_price),order_type=str(order_type),post_only=post_only,durable_before_post=True)
        return {'success':True,'orderID':order_id,'status':'MATCHED' if crossing else 'LIVE',
            'makingAmount':str(matched_size*fill_price if side=='BUY' else matched_size) if crossing else '0',
            'takingAmount':str(matched_size if side=='BUY' else matched_size*fill_price) if crossing else '0'}

    def check_token(self, token_id):
        assert str(token_id) in self.tokens

    def window_open(self, token_id=None):
        if token_id is None or not hasattr(self,'token_books'):
            return super().window_open()
        self.check_token(token_id)
        book=self.token_books[str(token_id)]
        restored=book['restored_window']
        return (self.now()<book['window_end']
            or restored is not None and restored[0]<=self.now()<restored[1])

    def get_balance_allowance(self, params):
        token=str(getattr(params,'token_id','') or '')
        if token:self.check_token(token)
        balance=int((self.inventory[token] if token else self.cash)*1_000_000)
        self.record('get_balance_allowance',token_id=token,balance=str(balance))
        return {'balance':str(balance),'allowance':str(balance)}

    def positions_payload(self):
        self.record('positions',inventory={token:str(size) for token,size in self.inventory.items()})
        return [{'asset':token,'size':str(size),'conditionId':self.tokens[token],
            'avgPrice':str(self.entry_prices[token]),'initialValue':str(size*self.entry_prices[token]),
            'currentValue':str(size*self.token_books[token]['bid']),
            'curPrice':str(self.token_books[token]['bid']),'redeemable':False,
            'outcome':'Yes' if int(token)%2==0 else 'No'}
            for token,size in self.inventory.items() if size>=Decimal('.01')]

    def account_rpc(self,url,method,params):
        from src.venue.polymarket_v2_adapter import ERC1155_BALANCE_OF_SELECTOR, ERC1155_IS_APPROVED_FOR_ALL_SELECTOR
        assert url=='https://scheduled-rpc.invalid' and method=='eth_call'
        assert len(params)==2 and params[1]=='latest'
        data=params[0]['data']
        selector=data[:10]
        self.record('account_rpc',selector=selector)
        if selector in {'0xdd62ed3e','0x70a08231'}:
            value=int(self.cash*1_000_000)
        elif selector==ERC1155_IS_APPROVED_FOR_ALL_SELECTOR:
            value=1
        elif selector==ERC1155_BALANCE_OF_SELECTOR:
            token=str(int(data[-64:],16))
            self.check_token(token)
            value=int(self.inventory[token]*1_000_000)
        else:
            raise AssertionError(f'unexpected offline RPC selector: {selector}')
        return '0x'+format(value,'064x')

    def confirm_trade(self,**kwargs):
        from copy import deepcopy
        payload=super().confirmed_trade_payload(**kwargs)
        matched=next(post for post in self.posts if post['order_id']==payload['taker_order_id'])
        payload['match_time']=matched['at']
        # Official CLOB TradeEvent reports the rate, not an exact cash fee.
        # Keep the existing external account model separate from native facts.
        payload.pop('fee_paid_micro')
        modeled_fee=(Decimal(payload['size'])*Decimal(payload['fee_rate_bps'])/10_000
            *Decimal(payload['price'])*(1-Decimal(payload['price']))).quantize(
                Decimal('0.00001'),rounding=ROUND_HALF_UP)
        modeled_fee_micro=int(modeled_fee*1_000_000)
        order=self.orders[payload['taker_order_id']]
        token=order['asset_id']
        assert Decimal(order['size_matched'])>0, 'only an actual matched order can confirm'
        payload['market']=order['market']
        payload['asset_id']=token
        payload['side']=order['side']
        payload['maker_orders'][0]['asset_id']=token
        payload['maker_orders'][0]['side']='SELL' if order['side']=='BUY' else 'BUY'
        self.confirmed_trades.append(deepcopy(payload))
        order['status']='MATCHED' if Decimal(order['size_matched'])==Decimal(order['original_size']) else 'CANCELED'
        quantity=Decimal(payload['size'])
        gross=quantity*Decimal(payload['price'])
        fee=Decimal(modeled_fee_micro)/1_000_000
        assert payload['id'] not in self._applied_trade_ids
        self._applied_trade_ids.add(payload['id'])
        if payload['side']=='BUY':
            previous=self.inventory[token]
            self.inventory[token]+=quantity
            self.cash-=gross+fee
            self.entry_prices[token]=(previous*self.entry_prices[token]+gross)/self.inventory[token]
        else:
            self.inventory[token]-=quantity
            self.cash+=gross-fee
        self.held_shares=self.inventory[self.held_token]
        self.average_entry_price=self.entry_prices[self.held_token]
        assert self.cash>=0 and all(size>=0 for size in self.inventory.values())
        self.record('native_confirmed',order_id=payload['taker_order_id'],trade_id=payload['id'],
            token_id=token,condition_id=order['market'],side=payload['side'],size=payload['size'],price=payload['price'],
            modeled_fee_micro=modeled_fee_micro,cash=str(self.cash),
            inventory={token:str(size) for token,size in self.inventory.items()})
        return payload

    def book(self, token_id):
        self.check_token(token_id)
        self.record('book', token_id=str(token_id))
        declared=self.token_books[str(token_id)]
        book={'asset_id':str(token_id), 'market':self.tokens[str(token_id)],
            'timestamp':str(int(self.now().timestamp()*1000)),
            'bids':[{'price':str(declared['bid']),'size':str(declared['bid_depth'])}] if self.window_open(token_id) else [],
            'asks':[{'price':str(declared['ask']),'size':str(declared['ask_depth'])}],
            'tick_size':'0.01', 'min_order_size':'1', 'neg_risk':False}
        import hashlib
        book['hash']=hashlib.sha256(json.dumps(book,sort_keys=True).encode()).hexdigest()
        return book

    def market(self, condition_id):
        assert condition_id in self.tokens.values()
        index = int(condition_id,16)-1
        self.record('market',condition_id=condition_id)
        return {'condition_id':condition_id,'question_id':'statistical-question-'+str(index),
            'active':True,'closed':False,'archived':False,'accepting_orders':True,'enable_order_book':True,
            'minimum_tick_size':'0.01','minimum_order_size':'1','neg_risk':False,
            'end_date_iso':self.market_end.isoformat(),'endDate':self.market_end.isoformat(),
            'tokens':[{'token_id':str(1000+index*2),'outcome':'Yes'}, {'token_id':str(1001+index*2),'outcome':'No'}]}

    def gamma_header(self):
        return {'id':'synthetic-family','slug':'highest-temperature-in-shanghai-on-october-2-2026',
            'title':'Highest temperature in Shanghai on October 2, 2026?',
            'endDate':self.market_end.isoformat(),'resolutionSource':self.case.city.settlement_source,
            'active':True,'closed':False}

    def gamma_markets(self):
        return [{'id':str(index+1), 'conditionId':'0x'+f'{index+1:064x}',
            'questionID':'statistical-question-'+str(index),
            'events':[self.gamma_header()],
            'question':f'Highest temperature in Shanghai on {self.case.request.target_date}: {item.bin_id}',
            'groupItemTitle':item.bin_id,'outcomes':json.dumps(['Yes','No']),
            'outcomePrices':json.dumps(['0.925','0.075'] if index==1 else ['0.5','0.5']),
            'clobTokenIds':json.dumps([str(1000+index*2),str(1001+index*2)]),
            'active':True,'closed':False,'archived':False,'acceptingOrders':True,'enableOrderBook':True,
            'negRisk':False,'orderPriceMinTickSize':.01,'orderMinSize':1,
            'endDate':self.market_end.isoformat(),
            'feeSchedule':{'exponent':1,'rate':.05,'takerOnly':True,'rebateRate':.25}}
            for index,item in enumerate(self.case.request.bins)]

    def install_httpx_transport(self, monkeypatch):
        sync, asynchronous = httpx.Client, httpx.AsyncClient
        def handler(request):
            if request.url.host == 'gamma-api.polymarket.com':
                self.record('gamma',path=request.url.path)
                if request.url.path == '/markets':
                    payload = self.gamma_markets()
                elif request.url.path == '/events':
                    payload = [{**self.gamma_header(),'markets':self.gamma_markets()}]
                else:
                    raise AssertionError(str(request.url))
                return httpx.Response(200,json=payload)
            assert request.url.host == 'scheduled-exit.invalid', str(request.url)
            return self.http_response(request.method,str(request.url),params=dict(request.url.params),
                body=json.loads(request.content) if request.content else None)
        def sync_factory(*args,**kwargs):
            kwargs['transport']=httpx.MockTransport(handler)
            return sync(*args,**kwargs)
        def async_factory(*args,**kwargs):
            kwargs['transport']=httpx.MockTransport(handler)
            return asynchronous(*args,**kwargs)
        monkeypatch.setattr(httpx,'Client',sync_factory)
        monkeypatch.setattr(httpx,'AsyncClient',async_factory)
        self.http_handler=handler

    def install_chain_transports(self, monkeypatch):
        from src.state import db
        from src.venue import polymarket_v2_adapter

        super().install_chain_transports(monkeypatch)
        positions_get=httpx.get
        def get(url,**kwargs):
            if str(url).startswith('https://data-api.polymarket.com/'):
                return positions_get(url,**kwargs)
            with HTTP_CLIENT(transport=httpx.MockTransport(self.http_handler)) as client:
                return client.get(url,**kwargs)
        monkeypatch.setattr(httpx,'get',get)
        # The capital sidecar uses the same declared eth_call facts in one
        # native batch. Keep the adapter's decoding and pUSD-only scope real.
        def batch(url,calls,*,timeout_seconds):
            assert timeout_seconds>0
            observing=getattr(self.case,'collateral_read_active',False)
            if observing:
                # Fresh collateral acquisition must not retain a canonical
                # TRADE/WORLD writer lease across its transport boundary.
                with db.trade_connection_with_world_flocked(write_class='live',blocking=False):pass
            results=[self.account_rpc(url,method,params) for method,params in calls]
            if observing:
                self.case.collateral_rpc_reads.append({'at':self.now().isoformat(),
                    'selectors':[params[0]['data'][:10] for _,params in calls],
                    'results':results,'writer_leases_available':True})
            return results
        monkeypatch.setattr(polymarket_v2_adapter,'_json_rpc_batch_call',batch)


def _bind_clock(case, monkeypatch):
    import importlib
    import datetime as runtime_datetime
    monkeypatch.setattr(runtime_datetime,'datetime',case.pump.clock_type)
    names = ('src.main','src.engine.event_reactor_adapter','src.engine.global_batch_runtime',
        'src.engine.global_auction_universe','src.engine.event_bound_final_intent','src.events.reactor',
        'src.events.event_store','src.engine.monitor_refresh','src.engine.cycle_runner','src.engine.cycle_runtime',
        'src.execution.exit_lifecycle','src.state.portfolio','src.riskguard.riskguard','src.state.db',
        'src.ingest.fill_synchronizer','src.ingest.price_channel_daemon','src.ingest.fill_cash_observer',
        'src.execution.command_recovery','src.state.chain_mirror_reconciler','src.state.chain_reconciliation',
        'src.execution.post_trade_capital','src.ingest.post_trade_capital_daemon','src.ingest.price_channel_ingest',
        'src.data.market_scanner','src.data.substrate_observer','src.state.snapshot_repo')
    for name in names:
        module=importlib.import_module(name)
        if hasattr(module,'datetime'):
            monkeypatch.setattr(module,'datetime',case.pump.clock_type)


def _capture_existing_anchor_as_raw_provider(case,monkeypatch):
    """Capture the same predeclared ECMWF body through its normal downloader."""
    from pathlib import Path
    from src.data import bayes_precision_fusion_download as download
    from src.data import openmeteo_client
    from src.data.replacement_current_value_serving import read_current_instrument_values
    request=case.native_request
    body=request.openmeteo_raw_payload_bytes
    def fetch(url,params,**kwargs):
        assert params['models']=='ecmwf_ifs'
        if 'capture_entity_body' in kwargs:
            kwargs['capture_entity_body'](body,case.clock[0].timestamp())
        if 'capture_network_response' in kwargs:
            kwargs['capture_network_response'](body,case.clock[0].timestamp(),{'content-type':'application/json'})
        return json.loads(body)
    target=download.BayesPrecisionFusionDownloadTarget(city=case.city.name,metric='high',
        target_date=str(request.target_date),lead_days=1,latitude=case.city.lat,longitude=case.city.lon,timezone_name=case.city.timezone)
    with monkeypatch.context() as transport:
        transport.setattr(download,'datetime',case.pump.clock_type)
        transport.setattr(openmeteo_client,'fetch',fetch)
        report=download.download_bayes_precision_fusion_extra_raw_inputs(
            forecast_db=Path(case.forecasts.execute('PRAGMA database_list').fetchone()[2]),
            cycle=request.source_cycle_time,targets=[target],models=['ecmwf_ifs'],
            include_previous_runs=False,prune_after=False,allow_single_runs_fallback=False,
            frozen_source_runs={'ecmwf_ifs':(request.source_cycle_time,request.openmeteo_source_available_at)})
    served=read_current_instrument_values(case.forecasts,city=case.city.name,metric='high',
        target_date=str(request.target_date),source_cycle_time_iso=request.source_cycle_time.isoformat(),
        decision_time_iso=case.clock[0].isoformat())
    assert 'ecmwf_ifs' in served,report
    assert served['ecmwf_ifs'].value_c==request.openmeteo_anchor.high_c
    return report



@pytest.mark.parametrize('scheduled_source',[
    {'forecast_inputs':True,'cash':10},
    {'forecast_inputs':True,'cash':20},
    {'forecast_inputs':True,'cash':20,'full_day0':True,'stale_hourly':True},
    {'forecast_inputs':True,'cash':20,'full_day0':True,'probability_handoff_only':True},
    {'forecast_inputs':True,'cash':20,'full_day0':True},
    {'forecast_inputs':True,'cash':20,'full_day0':True,'ordinary_reactor':True},
    {'forecast_inputs':True,'cash':20,'full_day0':True,'ordinary_reactor':True,'reentry_control':True},
    {'forecast_inputs':True,'cash':20,'full_day0':True,'ordinary_reactor':True,'collateral_contention':True},
    {'forecast_inputs':True,'cash':20,'full_day0':True,'ordinary_reactor':True,'capture_fault':'directory'},
    {'forecast_inputs':True,'cash':20,'full_day0':True,'ordinary_reactor':True,'capture_fault':'directory','first_exit_match_cap':'5'},
    {'forecast_inputs':True,'cash':20,'full_day0':True,'ordinary_reactor':True,'capture_fault':'directory','capture_correction':'revision_only'},
    {'forecast_inputs':True,'cash':20,'full_day0':True,'ordinary_reactor':True,'capture_fault':'directory','capture_correction':'downward'},
],indirect=True,ids=('cash10_control','cash20_entry','cash20_stale_hourly_control','cash20_probability_handoff','cash20_full_day0','cash20_registered_reactor','cash20_reentry_control','cash20_collateral_contention','cash20_unreadable_capture','cash20_unreadable_capture_partial','cash20_unreadable_capture_revision_only','cash20_unreadable_capture_downward'))
def test_pre_day0_normal_entry_with_declared_account_cash(scheduled_source,monkeypatch,record_property):
    from src.state import db
    from src.riskguard import riskguard
    from src.ingest import forecast_live_daemon as daemon
    from src.engine import event_reactor_adapter as adapter
    from src.events.event_store import EventStore
    from src.events import reactor
    from src.engine.event_bound_final_intent import submit_event_bound_final_intent_via_existing_executor
    case=scheduled_source
    _bind_clock(case,monkeypatch)
    assert case.trade.execute('SELECT COUNT(*) FROM position_current').fetchone()[0]==0
    assert case.trade.execute('SELECT COUNT(*) FROM venue_commands').fetchone()[0]==0
    venue=StatisticalVenue(case=case)
    record_property('declared_token_transport',json.dumps({'books':venue.token_books,
        'initial_inventory':venue.inventory,'cash':venue.cash,'fee_rate_bps':venue.fee_rate_bps},default=str))
    venue.install_httpx_transport(monkeypatch)
    venue.install_chain_transports(monkeypatch)
    monkeypatch.setattr(riskguard,'RISK_DB_PATH',case.tmp_path/'risk_state.db')
    controls=source._install_normal_execution_controls(case,monkeypatch,venue)
    monkeypatch.setattr(venue.adapter,'_rpc_call',venue.account_rpc)
    monkeypatch.setattr(venue.adapter,'polygon_rpc_url','https://scheduled-rpc.invalid')
    assert controls.ledger.refresh(venue.adapter).authority_tier=='CHAIN'
    level=riskguard.tick()
    assert level.value=='GREEN', level
    from src import risk_allocator
    from src.control import heartbeat_supervisor,ws_gap_guard
    risk_allocator.refresh_global_allocator(case.trade, ledger={'current_drawdown_pct':0.,'risk_level':level},
        heartbeat=heartbeat_supervisor.current_status(),ws_status=ws_gap_guard.summary())
    record_property('normal_anchor_capture',json.dumps(_capture_existing_anchor_as_raw_provider(case,monkeypatch),default=str))
    cfg=source._register_forecast_input_queues(case,monkeypatch)
    # The normal finalized-seed fence discovers sibling queue work by these
    # canonical directory names. Preserve that protocol in the isolated root.
    queue_root=cfg['seed_dir'].parent
    for key,name in {'seed_dir':'seeds','seed_processed_dir':'seed_processed',
        'seed_failed_dir':'seed_failed','request_dir':'requests','inflight_dir':'inflight',
        'processed_dir':'processed','failed_dir':'failed'}.items():
        cfg[key]=queue_root/name
        cfg[key].mkdir(parents=True,exist_ok=True)
    record_property('normal_queue_layout',json.dumps({key:str(value) for key,value in cfg.items()
        if key.endswith('_dir')}))
    case.materializer_cli_runs=source._run_materializer_cli_on_replay_clock(case,monkeypatch)
    case.queue_config=cfg
    if case.params.get('capture_fault'):
        _bind_capture_queue_sql_clock(case,monkeypatch)
        _observe_claimed_materializer_inputs(case,monkeypatch)
    source._stage_coherent_hourly_inputs(case,monkeypatch)
    case.values[0]=(26.,25.)
    for job in (daemon.REPLACEMENT_FORECAST_DISCOVERY_JOB_ID,
                daemon.REPLACEMENT_FORECAST_PRIORITY_MATERIALIZE_JOB_ID):
        case.pump.enable(job,at=case.clock[0]+timedelta(seconds=1))
    for _ in range(4):
        case.pump.advance(1)
        if case.forecasts.execute('SELECT COUNT(*) FROM forecast_posteriors').fetchone()[0]:break
    assert case.forecasts.execute('SELECT COUNT(*) FROM forecast_posteriors').fetchone()[0]>0
    from src import main
    with db.get_world_connection() as world:
        main._edli_emit_forecast_snapshot_events(world, decision_time=case.clock[0],
            received_at=case.clock[0].isoformat(),limit=None)
        world.commit()
        events=EventStore(world).fetch_pending_by_event_type(event_type='FORECAST_SNAPSHOT_READY',decision_time=case.clock[0].isoformat())
    assert events
    quote=reactor._edli_pre_submit_jit_book_quote_provider(case.trade)
    from src.solve import solver
    scoring_inputs=[]
    score=solver._score_global_single_order
    def observe_score(candidate,**kwargs):
        out=score(candidate,**kwargs)
        scoring_inputs.append({'token':candidate.token_id,'mode':candidate.execution_mode,
            'capital_limit':str(kwargs['capital_limit_usd']),
            'minimum_shares':str(solver._single_order_min_buy_shares(candidate)),
            'unit_cost':str(candidate.economic_cost_curve.fee_model.all_in_price(candidate.economic_cost_curve.levels[0].price)),
            'reason':out.no_trade_reason})
        return out
    monkeypatch.setattr(solver,'_score_global_single_order',observe_score)
    from src.runtime import reactor_wake
    wakes=reactor_wake.reactor_wakes_for_reason('forecast_posterior_advanced')
    world = db.get_world_connection()
    refresher=reactor._edli_decision_family_snapshot_refresher(case.forecasts)
    live=adapter.event_bound_live_adapter_from_trade_conn(case.trade,get_current_level=riskguard.get_current_level,
        calibration_conn=world,live_cap_conn=world,
        family_snapshot_refresher=refresher,
        producer_wake_ids=tuple(wake.wake_id for wake in wakes),
        forecast_conn=case.forecasts,topology_conn=case.forecasts,
        pre_submit_book_quote_provider=quote,
        pre_submit_authority_provider=reactor._edli_pre_submit_authority_provider_from_book_evidence_conn(case.trade,{},book_quote_provider=quote),
        executor_submit=lambda final_intent_cert,execution_command_cert:submit_event_bound_final_intent_via_existing_executor(
            final_intent_cert=final_intent_cert,execution_command_cert=execution_command_cert,
            conn=case.trade,snapshot_conn=case.trade,decision_time=case.clock[0]))
    from src.riskguard.risk_level import RiskLevel
    batches=[]
    process_batch=live.process_global_batch
    def observe_batch(*args,**kwargs):
        out=process_batch(*args,**kwargs)
        batches.append(out)
        return out
    live.process_global_batch=observe_batch
    runner=reactor.OpportunityEventReactor(EventStore(world),
        source_truth_gate=adapter.edli_source_truth_gate,
        executable_snapshot_gate=adapter.executable_snapshot_gate_from_trade_conn(case.trade,topology_conn=case.forecasts),
        riskguard_gate=adapter.riskguard_allows_new_entries(get_current_level=riskguard.get_current_level),
        cycle_entry_gate=lambda:riskguard.get_current_level()==RiskLevel.GREEN,
        final_intent_submit=live,reject=lambda event,stage,reason:None,family_snapshot_refresher=refresher)
    dispatch=runner.process_pending(decision_time=case.clock[0],targeted_event_ids=frozenset(event.event_id for event in events),targeted_only=True)
    assert batches,dispatch
    result=batches[0]
    record_property('entry_dispatch',str(dispatch))
    world.close()
    record_property('normal_entry_result',str(result))
    record_property('scoring_inputs',json.dumps(scoring_inputs))
    if case.params['cash']==20:
        assert venue.posts, {'reasons':[receipt.reason for receipt in result.receipts.values()],'scoring_inputs':scoring_inputs}
        assert len(venue.posts)==1 and venue.posts[0]['side']=='BUY'
        assert venue.posts[0]['token_id']==venue.held_token
        record_property('sdk_signed_entry',json.dumps(venue.posts))
        stream=_confirm_entry_through_registered_owners(case,monkeypatch,venue,controls,record_property)
        try:
            if case.params.get('full_day0'):
                _scheduled_day0_exit(case,monkeypatch,venue,controls,stream,record_property)
            _assert_collateral_scheduler_recovery(case,record_property)
        finally:
            record_property('native_collateral_snapshot_refresh',json.dumps({
                'calls':case.collateral_snapshot_calls,'rpc_reads':case.collateral_rpc_reads}))
            record_property('retryable_collateral_scheduler_events',json.dumps(
                case.pump.collateral_write_deferrals))
        return
    assert not venue.posts
    assert result.economic_cut_completed
    assert result.winner_event_id is None
    assert all(receipt.reason=='GLOBAL_AUCTION_NO_TRADE:NO_CURRENT_EXECUTABLE_POSITIVE_ORDER'
        for receipt in result.receipts.values())
    assert len(scoring_inputs)==6
    assert all(row['reason']=='DEPTH_INFEASIBLE' for row in scoring_inputs)
    middle=next(row for row in scoring_inputs if row['token']=='1003')
    assert Decimal(middle['capital_limit'])==Decimal('1')
    assert Decimal(middle['minimum_shares'])==Decimal('12.5')
    minimum_cost=Decimal(middle['minimum_shares'])*Decimal(middle['unit_cost'])
    assert minimum_cost==Decimal('1.046')
    assert minimum_cost>Decimal(middle['capital_limit'])
    assert case.trade.execute('SELECT COUNT(*) FROM venue_commands').fetchone()[0]==0
    assert case.trade.execute('SELECT COUNT(*) FROM position_current').fetchone()[0]==0
    artifact=json.loads(case.trade.execute("SELECT artifact_json FROM decision_log WHERE mode='global_single_order_auction' ORDER BY id DESC LIMIT 1").fetchone()[0])['summary']
    assert artifact['candidate_coverage_complete'] is True
    assert artifact['candidate_input_count']==6
    baseline=next(value['baseline'] for value in artifact['market_anchored_fit_artifact_audit']['source_identity_baselines'].values()
        if value['baseline']['token_id']=='1003')
    record_property('frozen_entry_capacity_control',json.dumps({'cash':'10','bid':'.07','ask':'.08','fee_rate':'.05',
        'native_depth_shares':'100','initial_q':baseline['q_raw'],'capital_limit_usd':middle['capital_limit'],
        'minimum_buy_all_in_usd':str(minimum_cost),'entry_at':case.clock[0].isoformat(),
        'outcome':'HOLD','canonical_positions':0,'commands':0,'sdk_posts':0}))


def _confirm_entry_through_registered_owners(case,monkeypatch,venue,controls,record_property):
    import asyncio
    from src.state import db,portfolio
    from src.ingest import polymarket_user_channel as user_channel
    monkeypatch.setattr(user_channel,'datetime',case.pump.clock_type)
    stream=user_channel.PolymarketUserChannelIngestor(venue.adapter,sorted(set(venue.tokens.values())),
        auth=user_channel.WSAuth('synthetic-key','synthetic-secret','synthetic-pass'),
        conn_factory=db.get_trade_connection_with_world)
    case.pump.register_fill_job()
    case.pump.register_main_monitor_jobs(reconcile=True)
    _register_chain_sync_read(case,record_property)
    _register_collateral_snapshot_refresh(case,record_property)
    # The literal sidecar registration is immediately due. Dispatch it before
    # delivering the next native confirmation, so no read is stamped earlier
    # than the external account change it observes.
    case.pump.advance()
    case.clock[0]+=timedelta(seconds=1)
    confirmed=venue.confirm_trade()
    assert 'fee_paid_micro' not in confirmed
    record_property('native_entry_confirmation',json.dumps({'trade':confirmed,'modeled_external_cash':str(venue.cash),
        'modeled_external_ctf_shares':str(venue.held_shares),'exact_fee_cash_receipt':'UNAVAILABLE; native TradeEvent contains fee_rate_bps only'}))
    asyncio.run(stream.handle_raw_message(json.dumps(confirmed)))
    case.pump.transport_ticks.append([case.clock[0]+timedelta(seconds=10),timedelta(seconds=10),
        lambda:asyncio.run(stream.handle_raw_message('PONG'))])
    events=case.pump.run_until(case.clock[0]+timedelta(seconds=90))
    record_property('entry_confirmation_jobs',json.dumps([(event.job_id,event.retval) for event in events],default=str))
    rows=case.trade.execute('SELECT position_id,phase,shares,fill_authority FROM position_current').fetchall()
    record_property('entry_confirmation_positions',json.dumps([dict(row) for row in rows]))
    assert len(rows)==1 and rows[0]['phase']=='active', [dict(row) for row in rows]
    assert rows[0]['shares']==pytest.approx(float(venue.held_shares))
    assert case.trade.execute("SELECT COUNT(*) FROM position_events WHERE event_type='ENTRY_ORDER_FILLED'").fetchone()[0]==1
    native_fact=case.trade.execute("SELECT state,filled_size,fill_price,fee_paid_micro,source FROM venue_trade_facts WHERE venue_order_id=?",(venue.posts[0]['order_id'],)).fetchone()
    assert native_fact['state']=='CONFIRMED' and native_fact['fee_paid_micro'] is None
    cash_facts=case.trade.execute('SELECT status,reason FROM venue_fill_cash_facts').fetchall()
    assert cash_facts and all(row['status']=='UNKNOWN' for row in cash_facts)
    record_property('entry_native_cash_authority',json.dumps({'trade_fact':dict(native_fact),
        'cash_facts':[dict(row) for row in cash_facts]}))
    case.position=portfolio.load_runtime_open_portfolio(case.trade).positions[0]
    from src.calibration.market_anchored_live_fit import load_held_entry_calibration,HeldSourceIdentityBinding
    with db.get_world_connection_read_only() as world:
        binding=load_held_entry_calibration(case.trade,position_id=case.position.trade_id,
            token_id=venue.held_token,side='NO',world_conn=world)
        assert isinstance(binding,HeldSourceIdentityBinding)
        certificate_types=[row[0] for row in world.execute('SELECT DISTINCT certificate_type FROM decision_certificates')]
    assert {'CalibrationCertificate','FinalIntentCertificate','ExecutionCommandCertificate','ExecutionReceiptCertificate'}<=set(certificate_types)
    attribution=case.trade.execute('SELECT position_id,command_id,resolution,source,intent_kind,decision_certificate_hash FROM position_decision_attribution').fetchone()
    assert attribution['resolution']=='ATTRIBUTED' and attribution['intent_kind']=='ENTRY'
    record_property('entry_calibration_lineage',json.dumps({'binding_type':type(binding).__name__,
        'attribution':dict(attribution),'certificate_types':sorted(certificate_types)}))
    return stream


def _register_chain_sync_read(case,record_property):
    """Compose the actual capital daemon's two-minute native read owner.

    A subprocess cannot inherit synthetic transport. Its unchanged child body
    runs in-process, retaining the literal registration/cadence and all native
    parsers, CTF reads, reconciliation, transactions and projection owners.
    This does not test OS-process isolation or manually warm chain authority.
    """
    import ast
    import inspect
    from src.execution import post_trade_capital
    from src.ingest import post_trade_capital_daemon
    case.chain_sync_calls=[]
    def observe_chain_sync():
        call={'at':case.clock[0].isoformat()}
        def positions():
            return [dict(row) for row in case.trade.execute('SELECT position_id,phase,shares,chain_shares,chain_state,chain_seen_at FROM position_current')]
        call['before']=positions()
        try:
            return post_trade_capital.chain_sync_read_cycle()
        except Exception as exc:
            call['error']=f'{type(exc).__name__}:{exc}'
            raise
        finally:
            call['after']=positions()
            case.chain_sync_calls.append(call)
    registration=next(node for node in ast.walk(ast.parse(inspect.getsource(post_trade_capital_daemon.main)))
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='add_job'
        and any(arg.arg=='id' and isinstance(arg.value,ast.Constant) and arg.value.value=='chain_sync_read' for arg in node.keywords))
    code=ast.fix_missing_locations(ast.Module(body=[ast.Expr(value=registration)],type_ignores=[]))
    exec(compile(code,post_trade_capital_daemon.__file__,'exec'),{
        **vars(post_trade_capital_daemon),'_scheduler':case.pump.scheduler,
        '_chain_sync_read_isolated':observe_chain_sync})
    job=case.pump.scheduler.get_job('chain_sync_read')
    assert job.trigger.interval==timedelta(minutes=2)
    record_property('native_chain_sync_registration',json.dumps({'registered_at':case.clock[0].isoformat(),
        'next_run_time':job.next_run_time.isoformat(),'interval_seconds':job.trigger.interval.total_seconds(),
        'owner':'post_trade_capital.chain_sync_read_cycle','process_isolation':'excluded; unchanged child body composed over fake transport'}))


def _register_collateral_snapshot_refresh(case,record_property):
    """Compose the actual thirty-second pUSD owner over the same raw RPC."""
    import ast
    import inspect
    from src.execution import post_trade_capital
    from src.ingest import post_trade_capital_daemon

    case.collateral_snapshot_calls=[]
    case.collateral_rpc_reads=[]
    case.collateral_read_active=False
    case.collateral_contention_injected=False
    def latest():
        row=case.trade.execute('SELECT id,captured_at,authority_tier,pusd_balance_micro,ctf_token_balances_json FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1').fetchone()
        return dict(row) if row else None
    def observe_refresh():
        call={'at':case.clock[0].isoformat(),'before':latest()}
        case.collateral_read_active=True
        blocker=None
        try:
            if (case.params.get('collateral_contention')
                    and not case.collateral_contention_injected
                    and case.clock[0]>=datetime(2026,10,1,18,tzinfo=UTC)):
                # A real raw writer holds SQLite's RESERVED lock across this
                # one callback. No owner, probability or outcome is replaced.
                blocker=sqlite3.connect(case.tmp_path/'zeus_trades.db',timeout=0)
                blocker.execute('BEGIN IMMEDIATE')
                case.collateral_contention_injected=True
                call['real_sqlite_contention']=True
            return post_trade_capital.collateral_snapshot_refresh_cycle()
        except Exception as exc:
            call['error']=f'{type(exc).__name__}:{exc}'
            raise
        finally:
            if blocker is not None:
                blocker.rollback()
                blocker.close()
            case.collateral_read_active=False
            call['after']=latest()
            case.collateral_snapshot_calls.append(call)
    registration=next(node for node in ast.walk(ast.parse(inspect.getsource(post_trade_capital_daemon.main)))
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='add_job'
        and any(arg.arg=='id' and isinstance(arg.value,ast.Constant) and arg.value.value=='collateral_snapshot_refresh' for arg in node.keywords))
    code=ast.fix_missing_locations(ast.Module(body=[ast.Expr(value=registration)],type_ignores=[]))
    exec(compile(code,post_trade_capital_daemon.__file__,'exec'),{
        **vars(post_trade_capital_daemon),'_scheduler':case.pump.scheduler,
        '_collateral_snapshot_refresh_isolated':observe_refresh})
    job=case.pump.scheduler.get_job('collateral_snapshot_refresh')
    assert job.trigger.interval==timedelta(seconds=30)
    record_property('native_collateral_refresh_registration',json.dumps({
        'registered_at':case.clock[0].isoformat(),'next_run_time':job.next_run_time.isoformat(),
        'interval_seconds':job.trigger.interval.total_seconds(),
        'owner':'post_trade_capital.collateral_snapshot_refresh_cycle',
        'process_isolation':'excluded; unchanged child body composed over fake transport'}))


def _assert_collateral_scheduler_recovery(case,record_property):
    """A recorded deferral must drain at the next real thirty-second callback."""
    recoveries=[]
    for event in case.pump.collateral_write_deferrals:
        failed_at=datetime.fromisoformat(event['scheduled_run_time'])
        failed=next(call for call in case.collateral_snapshot_calls
            if call['at']==event['observed_at'])
        assert failed.get('error') and failed['after']==failed['before'],failed
        retry_at=failed_at+timedelta(seconds=30)
        retry=next((call for call in case.collateral_snapshot_calls
            if datetime.fromisoformat(call['at'])==retry_at),None)
        assert retry is not None and 'error' not in retry,{'failure':event,'retry':retry}
        assert retry['after']['id']>retry['before']['id'],retry
        assert datetime.fromisoformat(retry['after']['captured_at'])==retry_at,retry
        assert retry['after']['authority_tier']=='CHAIN',retry
        recoveries.append({'failure':event,'retry':retry})
    if case.params.get('collateral_contention'):
        assert case.collateral_contention_injected
        assert len(recoveries)==1,recoveries
        assert datetime.fromisoformat(recoveries[0]['failure']['scheduled_run_time'])==datetime(2026,10,1,18,0,2,tzinfo=UTC)
    record_property('native_collateral_scheduler_recovery',json.dumps(recoveries))



def _recapture_hourly_inputs(case,monkeypatch,record_property):
    """Run existing deterministic/ENS fetch owners on declared repeated bodies."""
    from pathlib import Path
    from src.data import day0_hourly_vectors as hourly,openmeteo_model_updates as updates
    from src.data import openmeteo_client
    from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
    request=case.native_request
    bodies={}
    for model in hourly.day0_hourly_models_for_city(case.city):
        if model=='ecmwf_ifs':body=request.openmeteo_raw_payload_bytes
        else:
            row=case.forecasts.execute("SELECT artifact_path FROM raw_forecast_artifacts WHERE source_id=? AND artifact_metadata_json LIKE '%physical_response%' ORDER BY artifact_id LIMIT 1",
                (model+'_single_runs',)).fetchone()
            assert row is not None,model
            body=Path(row[0]).read_bytes()
        bodies[OPENMETEO_MODEL_IDS.get(model,model)]=body
    baseline=case.forecasts.execute("SELECT members_json,source_available_at FROM ensemble_snapshots WHERE city=? AND target_date=? AND temperature_metric='high' ORDER BY snapshot_id DESC LIMIT 1",
        (case.city.name,str(request.target_date))).fetchone()
    members=json.loads(baseline[0])
    payload={'latitude':case.city.lat,'longitude':case.city.lon,'timezone':case.city.timezone,
        'utc_offset_seconds':28800,'hourly_units':{'temperature_2m':'°C'},
        'hourly':{'time':[f'{request.target_date}T{hour:02d}:00' for hour in range(24)]}}
    for index,member in enumerate(members):
        payload['hourly']['temperature_2m' if index==0 else f'temperature_2m_member{index:02d}']=[member]*24
    ensemble_body=json.dumps(payload,sort_keys=True).encode()
    calls=[]
    def metadata(url,params,**kwargs):
        calls.append({'kind':'metadata','url':url,'at':case.clock[0].isoformat()})
        available=baseline[1] if 'ensemble' in url else request.openmeteo_source_available_at.isoformat()
        return {'last_run_initialisation_time':request.source_cycle_time.isoformat(),
            'last_run_availability_time':available,'last_run_modification_time':available,
            'update_interval_seconds':21600,'temporal_resolution_seconds':3600}
    def fetch(url,params,**kwargs):
        body=ensemble_body if url==hourly.OPENMETEO_ENSEMBLE_URL else bodies[params['models']]
        calls.append({'kind':'body','url':url,'params':params,'at':case.clock[0].isoformat()})
        if 'capture_entity_body' in kwargs:kwargs['capture_entity_body'](body,case.clock[0].timestamp())
        if 'capture_network_response' in kwargs:
            kwargs['capture_network_response'](body,case.clock[0].timestamp(),{'content-type':'application/json'})
        return json.loads(body)
    with monkeypatch.context() as transport:
        transport.setattr(updates,'_fetch_openmeteo',metadata)
        transport.setattr(openmeteo_client,'fetch',fetch)
        transport.setattr(hourly,'_day0_utc_now',lambda:case.clock[0])
        vectors,identity=hourly.fetch_day0_hourly_vectors(case.city,now=case.clock[0])
        assert len(vectors)==3,{'vectors':len(vectors),'calls':calls}
        assert hourly.persist_day0_hourly_vectors(vectors,target_date=str(request.target_date),conn=case.forecasts,
            request_hash=identity,endpoint=hourly.OPENMETEO_FORECAST_URL,now=case.clock[0])==3
        vectors,identity=hourly.fetch_day0_source_clock_ensemble_vectors(case.city,now=case.clock[0])
        assert len(vectors)==51,{'vectors':len(vectors),'calls':calls}
        assert hourly.persist_day0_hourly_vectors(vectors,target_date=str(request.target_date),conn=case.forecasts,
            request_hash=identity,endpoint=hourly.OPENMETEO_ENSEMBLE_URL,now=case.clock[0])==51
    case.forecasts.commit()
    record_property('day0_hourly_fetch_calls',json.dumps(calls))

def _register_ordinary_reactor(case):
    """Execute the literal ordinary main registration and its interval expression."""
    import ast
    import inspect
    from apscheduler.executors.debug import DebugExecutor
    from src import main
    scheduler=case.pump.scheduler
    scheduler.add_executor(DebugExecutor(),alias='reactor')
    nodes=list(ast.walk(ast.parse(inspect.getsource(main.main))))
    interval=next(node for node in nodes if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=='_edli_reactor_scan_interval_seconds' for target in node.targets))
    registration=next(node for node in nodes if isinstance(node,ast.Call)
        and isinstance(node.func,ast.Attribute) and node.func.attr=='add_job'
        and any(arg.arg=='id' and isinstance(arg.value,ast.Constant) and arg.value.value=='edli_event_reactor' for arg in node.keywords))
    code=ast.fix_missing_locations(ast.Module(body=[interval,ast.Expr(value=registration)],type_ignores=[]))
    exec(compile(code,main.__file__,'exec'),{**vars(main),'scheduler':scheduler,'edli_cfg':main._settings_section('edli',{})})


def _bind_capture_queue_sql_clock(case,monkeypatch):
    """SQLite and Python share the declared replay cut, including expiry checks."""
    from src.data import replacement_forecast_live_materialization_queue as queue
    connect=queue._queue_read_only_connection
    case.queue_clock_databases=[]
    def open_at_replay_clock(path):
        conn=connect(path)
        native=sqlite3.connect(':memory:',check_same_thread=False)
        case.queue_clock_databases.append(native)
        def strftime(fmt,value):
            return native.execute('SELECT strftime(?,?)',
                (fmt,case.clock[0].isoformat() if value=='now' else value)).fetchone()[0]
        conn.create_function('strftime',2,strftime)
        assert conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%S','now')").fetchone()[0]==case.clock[0].strftime('%Y-%m-%dT%H:%M:%S')
        return conn
    monkeypatch.setattr(queue,'_queue_read_only_connection',open_at_replay_clock)


def _observe_claimed_materializer_inputs(case,monkeypatch):
    """Retain exact inputs before normal success cleanup; execute the real CLI."""
    import hashlib
    import time
    from copy import deepcopy
    from pathlib import Path
    from scripts import materialize_replacement_forecast_live as cli
    from src.data import replacement_forecast_live_materialization_queue as queue
    from src.data import replacement_fusion_upgrade_trigger as trigger
    run=queue._run_command
    validate=cli._validated_request
    covered=queue._seed_already_covered
    case.materializer_input_trace=[]
    case.materializer_boundary_trace=[]
    case.materializer_coverage_trace=[]
    case.materializer_debt_trace=[]
    compare_debt=trigger.scope_capture_offers_larger_provider_set
    def observe_debt(*args,**kwargs):
        verdict=compare_debt(*args,**kwargs)
        case.materializer_debt_trace.append({'at':case.clock[0].isoformat(),
            'family':[kwargs['city'],kwargs['target_date'],kwargs['metric']],
            'verdict':deepcopy(verdict)})
        return verdict
    monkeypatch.setattr(trigger,'scope_capture_offers_larger_provider_set',observe_debt)
    def observe_coverage(**kwargs):
        result=covered(**kwargs)
        seed=kwargs['seed']
        case.materializer_coverage_trace.append({'at':case.clock[0].isoformat(),
            'computed_at':seed.get('computed_at'),'covered':result,
            'state':deepcopy(seed.get('day0_current_temperature_state'))})
        return result
    monkeypatch.setattr(queue,'_seed_already_covered',observe_coverage)
    def observe_validation(payload,**kwargs):
        request=validate(payload,**kwargs)
        case.materializer_boundary_trace.append({
            'at':case.clock[0].isoformat(),
            'input_current_state':deepcopy(payload.get('day0_current_temperature_state')),
            'input_revision_sources':deepcopy(payload.get('input_revision_sources')),
            'request_field_names':sorted(request.__dataclass_fields__),
            'request_current_state':getattr(request,'day0_current_temperature_state',None),
            'request_observed_extreme_c':request.day0_observed_extreme_c,
            'request_observed_extreme_source':request.day0_observed_extreme_source,
        })
        return request
    monkeypatch.setattr(cli,'_validated_request',observe_validation)
    def observe(argv):
        batch='--batch-input-json' in argv
        flag='--batch-input-json' if batch else '--input-json'
        start=argv.index(flag)+1
        end=next((i for i in range(start,len(argv)) if argv[i].startswith('--')),len(argv))
        paths=[Path(value) for value in argv[start:end]]
        assert paths and (batch or len(paths)==1)
        traces={}
        for path in paths:
            raw=path.read_bytes()
            trace={'started_at':case.clock[0].isoformat(),'input_path':str(path),
                'input_sha256':hashlib.sha256(raw).hexdigest(),'input':json.loads(raw),
                'wall_started':time.perf_counter(),'argv':list(argv)}
            traces[path]=trace
            case.materializer_input_trace.append(trace)
        result=run(argv)
        if batch:
            outcomes,errors=queue._parse_batch_envelopes(result.stdout)
            assert not errors,errors
        else:
            outcomes={paths[0]:result}
        for path,trace in traces.items():
            outcome=outcomes.get(path)
            trace.update({'finished_at':case.clock[0].isoformat(),
                'batch_returncode':result.returncode,'wall_finished':time.perf_counter(),
                'missing_envelope':outcome is None})
            if outcome is not None:
                trace.update({'returncode':outcome.returncode,'stdout':outcome.stdout,
                    'stderr':outcome.stderr,'response':json.loads(outcome.stdout) if outcome.stdout else {}})
        return result
    monkeypatch.setattr(queue,'_run_command',observe)


def _observe_capture_probability_preparation(case,monkeypatch):
    """Observe all normal probability uses without supplying a witness or q."""
    from copy import deepcopy
    import threading
    from src.engine import event_reactor_adapter as adapter
    from src.engine import current_day0_observation as current_source
    from src.engine import global_auction_universe as universe
    prepare=adapter._prepare_current_global_probability_family
    rebind=adapter._rebind_current_actuation_probability_tokens
    compare=adapter._global_probability_action_content_mismatches
    current_event=current_source.current_wrh_probability_event
    bind_monitor_tokens=universe._rebind_probability_witness_tokens
    case.capture_preparations=[]
    case.capture_rebindings=[]
    case.capture_comparisons=[]
    case.capture_monitor_token_rebindings=[]
    case.capture_actual_monitors=[]
    observed=threading.local()
    def witness_fields(witness):
        return {key:getattr(witness,key,None) for key in (
            'witness_identity','q_version','source_truth_identity','posterior_identity_hash',
            'probability_content_identity')}
    def observe_event(*args,**kwargs):
        event=current_event(*args,**kwargs)
        stack=getattr(observed,'stack',[])
        if stack:
            stack[-1].append({'type':event.event_type,'causal_snapshot_id':event.causal_snapshot_id,
                'payload':json.loads(event.payload_json)})
        return event
    monkeypatch.setattr(current_source,'current_wrh_probability_event',observe_event)
    def observe(*args,**kwargs):
        if not hasattr(observed,'stack'):observed.stack=[]
        source_reads=[]
        observed.stack.append(source_reads)
        try:
            prepared=prepare(*args,**kwargs)
        finally:
            observed.stack.pop()
        payload=kwargs.get('day0_payload_out') or {}
        carrier=payload.get('_edli_day0_source_clock_carrier_provenance') or {}
        witness=prepared.probability_witness
        case.capture_preparations.append({
            'at':kwargs['decision_time'].isoformat(),
            'use':str(kwargs.get('probability_use',adapter._CurrentProbabilityUse.ENTRY)),
            'posterior_id':prepared.posterior_id,'authority':prepared.probability_authority,
            'witness':witness_fields(witness),'qualified_source_reads':source_reads,
            'yes_point_q':list(getattr(witness,'yes_point_q',())),
            'bin_ids':list(getattr(witness,'bin_ids',())),
            'bindings':[{key:getattr(binding,key) for key in (
                'bin_id','condition_id','yes_token_id','no_token_id')}
                for binding in getattr(witness,'bindings',())],
            'source_clock_carrier':{key:deepcopy(carrier[key]) for key in (
                'posterior_id','probability_base_identity','carrier_written_inputs',
                'remaining_content_identity','remaining_carrier_probability_cutoff_utc') if key in carrier},
            'current_revision':payload.get('_edli_day0_current_temperature_source_revision_identity'),
            'direct_current':payload.get('_edli_day0_direct_current_redecision_authority'),
            'scope':payload.get('_edli_day0_redecision_authority_scope'),
            'pinned_reason':payload.get('_edli_day0_held_pinned_fallback_reason'),
        })
        return prepared
    monkeypatch.setattr(adapter,'_prepare_current_global_probability_family',observe)
    def observe_rebind(current,selected,**kwargs):
        result=rebind(current,selected,**kwargs)
        case.capture_rebindings.append({'at':case.clock[0].isoformat(),
            'required_token_id':kwargs.get('required_token_id'),
            'before':witness_fields(current),'selected':witness_fields(selected),
            'after':witness_fields(result)})
        return result
    monkeypatch.setattr(adapter,'_rebind_current_actuation_probability_tokens',observe_rebind)
    def observe_comparison(current,selected):
        result=compare(current,selected)
        case.capture_comparisons.append({'at':case.clock[0].isoformat(),
            'current':witness_fields(current),'selected':witness_fields(selected),
            'mismatches':list(result)})
        return result
    monkeypatch.setattr(adapter,'_global_probability_action_content_mismatches',observe_comparison)
    def observe_monitor_tokens(witness,**kwargs):
        result=bind_monitor_tokens(witness,**kwargs)
        case.capture_monitor_token_rebindings.append({'at':case.clock[0].isoformat(),
            'before':witness_fields(witness),'after':witness_fields(result),
            'token_map':dict(kwargs['token_map_by_condition'])})
        return result
    monkeypatch.setattr(universe,'_rebind_probability_witness_tokens',observe_monitor_tokens)


def _install_unreadable_capture(case,record_property):
    """A private filesystem fault, introduced after the lawful initial HOLD."""
    from src.data import replacement_forecast_live_materialization_queue as queue
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    requests=case.queue_config['request_dir']
    capture=requests.parent/queue._REQUEST_ALIAS_DIR/'.capture.scheduled-isolation'
    payload=capture/queue._CAPTURE_PAYLOAD_DIR
    payload.mkdir(parents=True)
    target=case.tmp_path/'unreadable-capture-target'
    target.write_bytes(b'capture isolation must never follow this alias')
    entry=payload/'bad.json'
    entry.symlink_to(target)
    fault={'capture':capture,'entry':entry,'target':target,'target_bytes':target.read_bytes(),
        'capture_inode':capture.stat().st_ino,'entry_inode':entry.lstat().st_ino,
        'blocked':capture,'restore_mode':0o700,'at':case.clock[0].isoformat(),
        'posterior_ids':{row[0] for row in case.forecasts.execute('SELECT posterior_id FROM forecast_posteriors')},
        'cli_index':len(case.materializer_input_trace)}
    case.capture_fault=fault
    owned,snapshot=read_current_noaa_wrh_snapshot(case.forecasts,city=case.city,
        target_date=str(case.request.target_date),as_of=case.clock[0])
    assert owned and snapshot is not None
    fault['prior_source']=snapshot.provenance()
    capture.chmod(0)
    assert not os.access(capture,os.R_OK), 'principal permission fault must be effective'
    for apply in (False,True):
        report=queue.reconcile_inflight_for_migration(request_path=requests,apply=apply)
        assert (capture.name,'unknown') in report.unsettled_captures
        assert not report.quiescent and not queue._capture_settled(capture)
    record_property('capture_fault_installed',json.dumps({
        'at':fault['at'],'capture_inode':fault['capture_inode'],'entry_inode':fault['entry_inode'],
        'classification':'UNKNOWN','after_initial_hold':True,'mode':0}))


def _record_capture_delivery_cut(case,venue,record_property):
    """Distinguish observed request→commit linkage from persisted source proof."""
    from pathlib import Path
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.data import replacement_forecast_live_materialization_queue as queue
    fault=case.capture_fault
    owned,snapshot=read_current_noaa_wrh_snapshot(case.forecasts,city=case.city,
        target_date=str(case.request.target_date),as_of=case.clock[0])
    assert owned and snapshot is not None
    revision=snapshot.response_sha256
    traces=case.materializer_input_trace[fault['cli_index']:]
    matched_post=next(post for post in reversed(venue.posts)
        if post['side']=='SELL' and post['token_id']==venue.held_token and Decimal(post['filled_size'])>0)
    candidates=[]
    forecast_path=Path(case.forecasts.execute('PRAGMA database_list').fetchone()[2])
    with sqlite3.connect(forecast_path.as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        for trace in traces:
            response=trace.get('response',{})
            identity=trace['input'].get('day0_current_temperature_state') or {}
            pid=response.get('posterior_id')
            if trace.get('returncode')!=0 or not response.get('committed') or pid is None:
                continue
            if datetime.fromisoformat(trace['finished_at'])>datetime.fromisoformat(matched_post['at']):
                continue  # A later commit is not contemporaneous action evidence.
            if pid in fault['posterior_ids']:
                continue
            # The scalar route does not consume current-temperature R. Match
            # its real scalar input here; actual action R is proved separately.
            if trace['input'].get('day0_observed_extreme_source')!=snapshot.source:
                continue
            if trace['input'].get('day0_observed_extreme_c')!=snapshot.extreme('high').value:
                continue
            if identity:
                assert identity.get('source_revision_identity')==revision
            row=conn.execute('SELECT posterior_id,computed_at,q_json,posterior_identity_hash,provenance_json FROM forecast_posteriors WHERE posterior_id=?',(pid,)).fetchone()
            assert row is not None, 'CLI success must be visible through a separate reader'
            provenance=json.loads(row['provenance_json'])
            conditioning=provenance.get('day0_conditioning') or {}
            consumed=[item for item in response['consumed_inputs']['files'] if item['role']=='request']
            assert len(consumed)==1 and consumed[0]['sha256']==trace['input_sha256']
            assert consumed[0]['path']==trace['input_path']
            assert response['forecast_family']==[case.city.name,str(case.request.target_date),'high']
            for key in ('observed_extreme_c','source','observation_time','sample_count','unit'):
                input_key='day0_'+key if key=='observed_extreme_c' else 'day0_observed_extreme_'+key
                assert conditioning[key]==trace['input'][input_key]
            assert snapshot.received_at<=datetime.fromisoformat(row['computed_at'])<=datetime.fromisoformat(trace['finished_at'])
            assert datetime.fromisoformat(trace['finished_at'])<=datetime.fromisoformat(matched_post['at'])
            persisted=(provenance.get('day0_current_temperature_state') or {}).get('source_revision_identity')
            candidates.append({'trace':trace,'posterior':dict(row),'persisted_source_revision':persisted})
    report=queue.reconcile_inflight_for_migration(request_path=case.queue_config['request_dir'],apply=True)
    assert (fault['capture'].name,'unknown') in report.unsettled_captures and not report.quiescent
    assert fault['capture'].stat().st_ino==fault['capture_inode']
    assert fault['capture'].stat().st_mode & 0o777==0
    assert fault['target'].read_bytes()==fault['target_bytes']
    fault['delivery']={'native_revision':revision,'native_received_at':snapshot.received_at.isoformat(),
        'request_to_commit':candidates,'preparations':case.capture_preparations,
        'cli_validation_boundary':case.materializer_boundary_trace,
        'coverage_attempts':case.materializer_coverage_trace,'rebindings':case.capture_rebindings,
        'simulated_match_at':matched_post['at'],'bid_window_end':venue.window_end.isoformat(),
        'attribution':'observed request-to-commit trace; persisted revision binding checked separately',
        'worker':'unchanged CLI composed synchronously; no OS worker restart claim'}
    record_property('capture_delivery_cut',json.dumps(fault['delivery'],default=str))
    assert candidates, 'qualified revision must reach a real successful CLI commit before the match'


def _assert_capture_restore(case,record_property):
    from src.data import replacement_forecast_live_materialization_queue as queue
    from src.ingest import forecast_live_daemon as daemon
    fault=case.capture_fault
    fault['blocked'].chmod(fault['restore_mode'])
    case.pump.enable(daemon.REPLACEMENT_FORECAST_MATERIALIZE_JOB_ID,at=case.clock[0])
    case.pump.advance()
    assert queue._capture_settled(fault['capture'])
    assert fault['entry'].is_symlink() and fault['entry'].lstat().st_ino==fault['entry_inode']
    assert fault['target'].read_bytes()==fault['target_bytes']
    record_property('capture_normal_reset',json.dumps({'at':case.clock[0].isoformat(),
        'normal_registered_queue_reread':True,'entry_inode_unchanged':True,'alias_target_unchanged':True}))


def _assert_capture_reset(case,record_property):
    from src.data import replacement_forecast_live_materialization_queue as queue
    _assert_capture_restore(case,record_property)
    fault=case.capture_fault
    proof=fault['delivery']
    from pathlib import Path
    forecast_path=Path(case.forecasts.execute('PRAGMA database_list').fetchone()[2])
    coverage=[queue._seed_already_covered(forecast_db=forecast_path,seed=row['trace']['input'])
        for row in proof['request_to_commit']]
    committed=min(datetime.fromisoformat(row['trace']['finished_at']) for row in proof['request_to_commit'])
    later=[row for row in case.materializer_debt_trace
        if datetime.fromisoformat(row['at'])>committed
        and row['family']==[case.city.name,str(case.request.target_date),'high']]
    debt_keys={'day0_current_temperature_state','day0_scalar_conditioning'}
    repeated=[row for row in later if debt_keys.intersection(row['verdict'].get('changed_input_sources',()))]
    record_property('capture_materialization_reset',json.dumps({'completed_request_coverage':coverage,
        'debt_checks_after_commit':later,'repeated_physical_debt':repeated,
        'sqlite_clock':case.clock[0].isoformat(),'python_clock':case.clock[0].isoformat(),
        'scope':'unchanged scalar/current inputs only; independent provider/vector obligations remain'}))
    assert later
    assert all(coverage), 'the actual completed request must satisfy unchanged authority and readiness coverage'
    assert not repeated, 'unchanged consumed physical input cannot recreate impossible materialization debt'


def _assert_capture_source_correction(case,record_property,reactor_invocations):
    """A real native correction must redecide before the five-minute fallback."""
    from pathlib import Path
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.data import replacement_forecast_live_materialization_queue as queue
    fault=case.capture_fault
    owned,snapshot=read_current_noaa_wrh_snapshot(case.forecasts,city=case.city,
        target_date=str(case.request.target_date),as_of=case.clock[0])
    assert owned and snapshot is not None
    current=snapshot.provenance()
    prior=fault['prior_source']
    assert current['response_sha256']!=prior['response_sha256']
    assert [row['utc'] for row in current['rows']]==[row['utc'] for row in prior['rows']]
    expected=26 if case.params['capture_correction']=='revision_only' else 25
    assert snapshot.extreme('high').value==expected
    prepared=[row for row in case.capture_preparations
        if datetime.fromisoformat(row['at'])>=snapshot.received_at
        and row['use']=='held_monitor'
        and any(read['causal_snapshot_id']=='current_wrh_product:'+snapshot.response_sha256
            for read in row['qualified_source_reads'])]
    assert prepared, 'qualified corrected R must reach a normal probability preparation'
    assert all(datetime.fromisoformat(row['at'])<=case.clock[0] for row in prepared)
    monitor_cuts=[]
    for row in case.trade.execute("SELECT artifact_json FROM decision_log WHERE mode='exit_monitor' AND started_at>=?",
        (snapshot.received_at.isoformat(),)):
        artifact=json.loads(row[0])
        monitor_cuts.extend({'at':artifact['started_at'],**result}
            for result in artifact.get('monitor_results',[]) if result['position_id']==case.position.trade_id)
    assert any(row.get('fresh_prob') is not None and row.get('exit_reason')!='EVIDENCE_UNAVAILABLE'
        for row in monitor_cuts),monitor_cuts
    position=dict(case.trade.execute('SELECT last_monitor_prob,last_monitor_prob_is_fresh,phase FROM position_current WHERE position_id=?',
        (case.position.trade_id,)).fetchone())
    assert position['last_monitor_prob_is_fresh']==1
    # Preparation precedes the normal monitor's CLOB-token rebinding. Follow
    # that real before/after identity into the actual consumed monitor witness.
    matched_monitor=[]
    matched_cuts=set()
    for prepared_row in sorted(prepared,key=lambda row:row['at'],reverse=True):
        rebound=[bound for bound in case.capture_monitor_token_rebindings
            if bound['before']==prepared_row['witness']
            and datetime.fromisoformat(prepared_row['at'])<=datetime.fromisoformat(bound['at'])]
        for actual in case.capture_actual_monitors:
            if actual['position_id']!=case.position.trade_id or not actual['fresh']:
                continue
            actual_bindings=[bound for bound in rebound
                if bound['after']['witness_identity']==actual['witness_identity']
                and datetime.fromisoformat(bound['at'])<=datetime.fromisoformat(actual['at'])]
            if not actual_bindings:
                continue
            bindings=[binding for binding in actual['bindings']
                if binding['condition_id']==case.position.condition_id
                and binding['no_token_id']==case.position.no_token_id]
            assert len(bindings)==1
            held_bin=bindings[0]['bin_id']
            q=1-actual['yes_point_q'][actual['bin_ids'].index(held_bin)]
            assert q==pytest.approx(actual['q'],abs=1e-12)
            bound=max(actual_bindings,key=lambda row:row['at'])
            for monitor_row in monitor_cuts:
                key=(actual['position_id'],actual['at'],actual['witness_identity'],monitor_row['at'])
                if (key not in matched_cuts and actual['at']==monitor_row['at']
                    and monitor_row.get('fresh_prob') is not None
                    and q==pytest.approx(monitor_row['fresh_prob'],abs=1e-12)):
                    matched_cuts.add(key)
                    matched_monitor.append({'prepared':prepared_row,'token_rebinding':bound,
                        'consumed':actual,'persisted_monitor':monitor_row})
    assert matched_monitor, 'the corrected source must be consumed by the same held-monitor cut'
    traces=[trace for trace in case.materializer_input_trace[fault['cli_index']:]
        if trace.get('returncode')==0 and trace.get('response',{}).get('committed')
        and datetime.fromisoformat(trace['finished_at'])>=snapshot.received_at
        and trace['input'].get('day0_observed_extreme_c')==expected]
    checks=[row for row in case.materializer_debt_trace
        if datetime.fromisoformat(row['at'])>=snapshot.received_at
        and row['family']==[case.city.name,str(case.request.target_date),'high']]
    report={'prior_source':prior,'corrected_source':current,'preparations':prepared,
        'held_monitor_cuts':monitor_cuts,'matched_held_monitor':matched_monitor,
        'current_position':position,'actual_cli':traces,'debt_checks':checks,
        'reactor_invocations':reactor_invocations,'observed_until':case.clock[0].isoformat(),
        'scope':'normal registered source/delivery/queue/monitor owners; synchronous CLI; simulated event clock'}
    record_property('capture_same_clock_correction',json.dumps(report,default=str))
    assert checks
    if case.params['capture_correction']=='downward':
        assert traces, 'same-clock scalar decrease requires a genuine fast successor CLI commit'
        forecast_path=Path(case.forecasts.execute('PRAGMA database_list').fetchone()[2])
        with sqlite3.connect(forecast_path.as_uri()+'?mode=ro',uri=True) as reader:
            for trace in traces:
                response=trace['response']
                consumed=[item for item in response['consumed_inputs']['files'] if item['role']=='request']
                assert len(consumed)==1 and consumed[0]['sha256']==trace['input_sha256']
                assert consumed[0]['path']==trace['input_path']
                assert response['forecast_family']==[case.city.name,str(case.request.target_date),'high']
                row=reader.execute('SELECT computed_at,provenance_json FROM forecast_posteriors WHERE posterior_id=?',
                    (response['posterior_id'],)).fetchone()
                assert row is not None
                conditioning=json.loads(row[1])['day0_conditioning']
                for key in ('observed_extreme_c','source','observation_time','sample_count','unit'):
                    input_key='day0_'+key if key=='observed_extreme_c' else 'day0_observed_extreme_'+key
                    assert conditioning[key]==trace['input'][input_key]
                assert snapshot.received_at<=datetime.fromisoformat(row[0])<=datetime.fromisoformat(trace['finished_at'])<=case.clock[0]
        committed={trace['response']['posterior_id'] for trace in traces}
        assert any(row['posterior_id'] in committed for row in prepared)
        assert all(queue._seed_already_covered(forecast_db=forecast_path,seed=trace['input']) for trace in traces)
        assert any('day0_scalar_conditioning' in trace['input'].get('input_revision_sources',()) for trace in traces)
        assert all(trace['input'].get('day0_current_temperature_state') is None for trace in traces)
        first_commit=min(datetime.fromisoformat(trace['finished_at']) for trace in traces)
        checks=[row for row in checks if datetime.fromisoformat(row['at'])>first_commit]
    assert checks
    assert not [row for row in checks if {'day0_current_temperature_state','day0_scalar_conditioning'}
        .intersection(row['verdict'].get('changed_input_sources',()))]
    classified=queue.reconcile_inflight_for_migration(request_path=case.queue_config['request_dir'],apply=True)
    assert (fault['capture'].name,'unknown') in classified.unsettled_captures and not classified.quiescent
    assert fault['capture'].stat().st_mode & 0o777==0
    assert fault['capture'].stat().st_ino==fault['capture_inode']
    assert fault['target'].read_bytes()==fault['target_bytes']
    _assert_capture_restore(case,record_property)


def _assert_capture_action_attribution(case,venue,intent,artifact,record_property,*,post=None):
    """Bind the scalar posterior and independently re-read current source to SELL."""
    proof=case.capture_fault['delivery']
    receipt=intent['exit_intent_probability_receipt']
    capital=intent['exit_intent_capital_certificate']
    winner=artifact['summary']['proof_counterfactual']['winner']['evaluation']
    post=post or venue.posts[1]
    rebindings=[row for row in case.capture_rebindings
        if row['required_token_id']==venue.held_token
        and row['selected']['probability_content_identity']==receipt['probability_content_identity']
        and datetime.fromisoformat(row['at'])<=datetime.fromisoformat(post['at'])]
    prepared=[row for row in case.capture_preparations
        if any(row['witness']['witness_identity']==bound['before']['witness_identity'] for bound in rebindings)]
    pids={row['posterior']['posterior_id'] for row in proof['request_to_commit']}
    record_property('capture_action_attribution',json.dumps({'receipt':receipt,
        'rebindings':rebindings,'prepared':prepared,'comparisons':case.capture_comparisons,
        'committed_posterior_ids':sorted(pids)},default=str))
    assert rebindings and prepared
    assert all(any(compared['current']==bound['after'] and compared['selected']==bound['selected']
        and compared['mismatches']==[] for compared in case.capture_comparisons) for bound in rebindings)
    assert all(row['posterior_id'] in pids for row in prepared)
    assert all(any(read['causal_snapshot_id']=='current_wrh_product:'+proof['native_revision']
        for read in row['qualified_source_reads']) for row in prepared)
    for row in prepared:
        index=row['bin_ids'].index(receipt['payoff_q_correction']['bin_id'])
        assert 1-row['yes_point_q'][index]==pytest.approx(receipt['held_side_probability'],abs=1e-12)
    assert winner['probability_witness_identity']==receipt['probability_witness_identity']
    for key in ('probability_content_identity','probability_witness_identity','q_version','source_truth_identity'):
        assert capital[key]==receipt[key]
    assert 0<receipt['held_side_probability']<float(venue.bid)


def _assert_capture_partial_closure(case,venue,stream,confirmations,record_property):
    """Observe normal residual ownership after a declared native partial match."""
    import asyncio
    from dataclasses import asdict
    from src.ingest import polymarket_user_channel as user_channel
    from src.state import db
    from src.state.fill_dedup import economic_exit_fills_for_position
    case.pump.run_until(venue.window_end+timedelta(seconds=92))
    held=[post for post in venue.posts if post['token_id']==venue.held_token and post['side']=='SELL']
    record_property('capture_partial_transport',json.dumps({'posts':venue.posts,
        'confirmations':confirmations,'bid_window_end':venue.window_end.isoformat(),
        'declared_first_match_cap':case.params['first_exit_match_cap'],
        'native_confirmation_min_delay_seconds':2,'transport_tick_seconds':1,
        'observed_until':case.clock[0].isoformat()},default=str))
    assert len(held)==2, 'the actual owners must reauthorize and match the residual within the original window'
    assert [Decimal(post['filled_size']) for post in held]==[Decimal('5'),Decimal('7.5')]
    assert [Decimal(post['size']) for post in held]==[Decimal('12.5'),Decimal('7.5')]
    assert all(datetime.fromisoformat(post['at'])<venue.window_end for post in held)
    assert all(post['order_type']=='FAK' and not post['post_only'] for post in held)
    assert not [post for post in venue.posts[1:] if post['side']=='BUY' and post['token_id']==venue.held_token]
    first=next(row for row in confirmations if row['payload']['taker_order_id']==held[0]['order_id'])
    assert first['terminal_order']['status']=='CANCELED'
    assert first['external_inventory'][venue.held_token]=='7.5'
    assert datetime.fromisoformat(first['at'])<datetime.fromisoformat(held[1]['at'])
    intermediate=[row for row in case.partial_position_trace
        if datetime.fromisoformat(first['at'])<=datetime.fromisoformat(row['at'])<datetime.fromisoformat(held[1]['at'])
        and row['position']['shares']==7.5 and row['position']['phase']!='economically_closed']
    assert intermediate, 'canonical partial projection must precede normal residual authorization'
    record_property('capture_partial_intermediate_projection',json.dumps(intermediate))
    commands=[dict(case.trade.execute('SELECT * FROM venue_commands WHERE command_id=?',
        (post['command_id'],)).fetchone()) for post in held]
    assert commands[0]['state'] in {'EXPIRED','CANCELED'}
    assert commands[1]['state']=='FILLED'
    economic=economic_exit_fills_for_position(case.trade,case.position.trade_id)
    assert len(economic)==2 and sum(fill.quantity for fill in economic)==Decimal('12.5')
    assert {fill.command_id for fill in economic}=={post['command_id'] for post in held}
    assert {fill.venue_order_id for fill in economic}=={post['order_id'] for post in held}
    for post in held:
        native=next(row['payload'] for row in confirmations if row['payload']['taker_order_id']==post['order_id'])
        fill=next(fill for fill in economic if fill.command_id==post['command_id'])
        fact=dict(case.trade.execute('SELECT * FROM venue_trade_facts WHERE command_id=? AND trade_id=?',
            (post['command_id'],native['id'])).fetchone())
        assert fact['state']=='CONFIRMED' and fact['venue_order_id']==post['order_id']
        assert fill.trade_id==fact['trade_id']==native['id']
        assert fill.quantity==Decimal(native['size'])==Decimal(str(fact['filled_size']))==Decimal(post['filled_size'])
        assert fill.unit_price==Decimal(native['price'])==Decimal(str(fact['fill_price']))==Decimal(post['fill_price'])
        assert fill.notional==fill.quantity*fill.unit_price
        assert native['asset_id']==post['token_id'] and native['side']==post['side']
    position=dict(case.trade.execute('SELECT * FROM position_current WHERE position_id=?',
        (case.position.trade_id,)).fetchone())
    assert position['chain_shares']==0 and position['phase']=='economically_closed'
    record_property('capture_partial_canonical_closure',json.dumps({'commands':commands,
        'economic_fills':[asdict(fill) for fill in economic],'position':position},default=str))
    _record_capture_delivery_cut(case,venue,record_property)
    events=[dict(row) for row in case.trade.execute(
        'SELECT sequence_no,event_type,payload_json FROM position_events WHERE position_id=? ORDER BY sequence_no',
        (case.position.trade_id,))]
    receipts=[]
    from src.control.live_health import _current_global_auction_candidate_payload
    for post in held:
        posted=next(row for row in events if row['event_type']=='EXIT_ORDER_POSTED'
            and json.loads(row['payload_json']).get('last_exit_command_id')==post['command_id'])
        intent=json.loads([row for row in events if row['event_type']=='EXIT_INTENT'
            and row['sequence_no']<posted['sequence_no']][-1]['payload_json'])
        assert Decimal(str(intent['exit_intent_shares']))==Decimal(post['size'])
        receipt=intent['exit_intent_capital_certificate']['global_auction_receipt']
        artifact=json.loads(case.trade.execute('SELECT artifact_json FROM decision_log WHERE id=?',
            (receipt['decision_log_id'],)).fetchone()[0])
        capital=intent['exit_intent_capital_certificate']
        command=next(command for command in commands if command['command_id']==post['command_id'])
        summary=artifact['summary']
        candidates=_current_global_auction_candidate_payload(case.trade,summary)
        selected=next(candidate for candidate in candidates['detailed']
            if candidate['candidate_id']==receipt['winner_candidate_id'])
        assert selected['status']=='SELECTED' and selected['action']==capital['action']==command['side']=='SELL'
        assert selected['token_id']==capital['token_id']==command['token_id']==post['token_id']==venue.held_token
        assert selected['position_id']==capital['position_id']==command['position_id']==case.position.trade_id
        assert capital['condition_id']==post['condition_id']==venue.tokens[venue.held_token]
        assert capital['candidate_id']==receipt['winner_candidate_id']==summary['winner_candidate_id']
        assert capital['actuation_identity']==receipt['winner_actuation_identity']==summary['winner_actuation_identity']
        assert receipt['receipt_hash']==summary['receipt_hash']
        assert receipt['execution_binding_hash']==summary['execution_binding_hash']
        assert Decimal(capital['selected_shares'])==Decimal(post['size'])==Decimal(str(command['size']))
        assert Decimal(capital['exact_limit_price'])==Decimal(post['price'])==Decimal(str(command['price']))
        assert capital['expected_sell_delta_log_wealth']>0 and capital['expected_sell_ev_usd']>0
        assert capital['held_probability_point']==selected['q_served']==intent['exit_intent_probability_receipt']['held_side_probability']
        _assert_capture_action_attribution(case,venue,intent,artifact,record_property,post=post)
        receipts.append(receipt)
    assert receipts[0]!=receipts[1], 'residual must consume its own normal-owner selection receipt'
    facts=[tuple(row) for row in case.trade.execute('SELECT * FROM venue_trade_facts ORDER BY trade_fact_id')]
    command_rows=[tuple(row) for row in case.trade.execute('SELECT * FROM venue_commands ORDER BY command_id')]
    positions=[tuple(row) for row in case.trade.execute('SELECT * FROM position_current ORDER BY position_id')]
    event_counts=tuple(case.trade.execute('SELECT (SELECT COUNT(*) FROM venue_command_events),(SELECT COUNT(*) FROM position_events)').fetchone())
    post_count=len(venue.posts)
    restarted=user_channel.PolymarketUserChannelIngestor(venue.adapter,sorted(set(venue.tokens.values())),
        auth=stream.auth,conn_factory=db.get_trade_connection_with_world)
    for payload in venue.confirmed_trades:
        late={**payload,'timestamp':str(int(case.clock[0].timestamp()))}
        assert asyncio.run(stream.handle_raw_message(json.dumps(late)))['reason']=='duplicate_trade_fact'
        assert asyncio.run(restarted.handle_raw_message(json.dumps(late)))['reason']=='duplicate_trade_fact'
    assert facts==[tuple(row) for row in case.trade.execute('SELECT * FROM venue_trade_facts ORDER BY trade_fact_id')]
    assert command_rows==[tuple(row) for row in case.trade.execute('SELECT * FROM venue_commands ORDER BY command_id')]
    assert positions==[tuple(row) for row in case.trade.execute('SELECT * FROM position_current ORDER BY position_id')]
    assert event_counts==tuple(case.trade.execute('SELECT (SELECT COUNT(*) FROM venue_command_events),(SELECT COUNT(*) FROM position_events)').fetchone())
    assert post_count==len(venue.posts)
    cash_facts=[dict(row) for row in case.trade.execute('SELECT status,reason FROM venue_fill_cash_facts')]
    assert cash_facts and all(row['status']=='UNKNOWN' for row in cash_facts)
    assert all(row[0] is None for row in case.trade.execute('SELECT fee_paid_micro FROM venue_trade_facts'))
    record_property('capture_partial_duplicate_restart',json.dumps({'confirmed_trades':len(venue.confirmed_trades),
        'canonical_fills_positions_commands_events_unchanged':True,'sdk_posts_unchanged':True,
        'cash_facts':cash_facts,'scope':'receive-handler memory restart; no daemon or OS restart'}))
    _assert_capture_reset(case,record_property)


def _scheduled_day0_exit(case,monkeypatch,venue,controls,stream,record_property):
    import asyncio
    import threading
    import time
    from src import main
    from src.data import station_temperature_adapters as upstream
    from src.execution import exit_lifecycle
    from src.engine import cycle_runner
    from src.engine import monitor_refresh
    from src.state import db,portfolio
    from src.riskguard import riskguard
    from src.runtime import bankroll_provider
    from src.control import ws_gap_guard
    from src.engine.global_auction_universe import WorkContext,WorkDeferred
    from src.events import reactor

    if case.params.get('capture_fault'):
        _observe_capture_probability_preparation(case,monkeypatch)

    reactor_invocations=[]
    run_reactor=reactor.run_edli_event_reactor_cycle
    def observe_reactor(**kwargs):
        from dataclasses import asdict
        call={'at':case.clock[0].isoformat(),
            'reason':kwargs.get('producer_wake_reason'),
            'wake_ids':kwargs.get('producer_wake_ids'),
            'families':kwargs.get('producer_wake_families'),
            'entry_block_reason':kwargs.get('live_entry_block_reason'),
            'entry_family_blocks':kwargs.get('live_entry_family_block_reasons'),
            'requests':[asdict(value) for value in kwargs.get('producer_held_sell_reauction_requests',())]}
        reactor_invocations.append(call)
        try:
            result=run_reactor(**kwargs)
            call['result']=result
            return result
        finally:
            call['finished_at']=case.clock[0].isoformat()
    monkeypatch.setattr(reactor,'run_edli_event_reactor_cycle',observe_reactor)

    constructor_deferrals=[]
    checkpoint=WorkContext.checkpoint
    def observe_checkpoint(context,stage):
        try:
            return checkpoint(context,stage)
        except WorkDeferred as exc:
            if str(stage).startswith('reactor_construct:'):
                constructor_deferrals.append({'at':case.clock[0].isoformat(),'stage':exc.stage,
                    'code':exc.code.value,'source':exc.source})
            raise
    monkeypatch.setattr(WorkContext,'checkpoint',observe_checkpoint)

    threads=case.threads
    submit=exit_lifecycle.execute_exit_order
    def advance_execution_clock(*args,**kwargs):
        # The deterministic scheduler spends no event time inside Python;
        # preserve the real intent-before-command ordering at submission.
        case.clock[0]+=timedelta(microseconds=1)
        return submit(*args,**kwargs)
    monkeypatch.setattr(exit_lifecycle,'execute_exit_order',advance_execution_clock)
    monkeypatch.setattr(exit_lifecycle,'_held_monitor_clob_client',lambda:venue.client)
    monkeypatch.setattr(exit_lifecycle,'_utcnow',lambda:case.clock[0])
    monkeypatch.setattr(cycle_runner,'ZEUS_WORLD_DB_PATH',db.ZEUS_WORLD_DB_PATH)
    monkeypatch.setattr(cycle_runner,'_zeus_trade_db_path',db._zeus_trade_db_path)
    observed_snapshots=[]
    materialize_probability=monitor_refresh._materialize_current_global_day0_probability
    def observe_probability(position,snapshot):
        result=None
        try:
            result=materialize_probability(position,snapshot)
            return result
        finally:
            if case.params.get('capture_fault'):
                witness=snapshot.witness
                case.capture_actual_monitors.append({'at':case.clock[0].isoformat(),
                    'position_id':position.trade_id,'witness_identity':witness.witness_identity,
                    'bindings':[{key:getattr(binding,key) for key in (
                        'bin_id','condition_id','yes_token_id','no_token_id')} for binding in witness.bindings],
                    'bin_ids':list(witness.bin_ids),'yes_point_q':list(getattr(witness,'yes_point_q',())),
                    'q':result[0] if result is not None else None,
                    'fresh':result[2] if result is not None else False})
            observed_snapshots.append({'authority':snapshot.probability_authority,
                'binding':snapshot.day0_payload.get('_edli_global_day0_binding'),
                'bundle':snapshot.day0_payload.get('_edli_day0_causal_evidence_bundle'),
                'vector':snapshot.day0_payload.get('_edli_day0_remaining_vector_witness'),
                'validation':snapshot.day0_payload.get('_edli_day0_causal_evidence_bundle_validation')})
    monkeypatch.setattr(monitor_refresh,'_materialize_current_global_day0_probability',observe_probability)
    main._edli_initialize_reactor_wake_cursor()
    # The normal listener's one-second transport is driven below. Its actual
    # poll/dispatch owners run; no socket daemon or background timer is started.
    monkeypatch.setattr(main,'_edli_reactor_wake_thread',SimpleNamespace(is_alive=lambda:True))
    def poll():
        main._value_standing_entries_for_new_wakes()
        main._consume_live_control_commands()
        if main._service_pending_collateral_authority_wake() is None:
            main._edli_reactor_wake_poll_once()
        main._retire_served_reactor_wakes()
        for thread in list(threads):
            if thread.name=='held-position-monitor-recovery':continue
            thread.join(15)
            assert not thread.is_alive(),thread.name

    day0=datetime(2026,10,1,18,tzinfo=UTC)
    # Re-present unchanged external bodies before Day0. The prelude's14:58
    # hourly capture would otherwise be3h02m old at18:00, beyond its3h law.
    # Source issue/availability and bodies stay fixed. Both existing fetch
    # producers run over fake external transport, retaining normal cache rules.
    if not case.params.get('stale_hourly'):
        case.clock[0]=day0-timedelta(minutes=2)
        _recapture_hourly_inputs(case,monkeypatch,record_property)
        record_property('day0_hourly_native_recapture',json.dumps({'captured_at':case.clock[0].isoformat(),
            'source_cycle_time':case.native_request.source_cycle_time.isoformat(),
            'source_available_at':case.native_request.openmeteo_source_available_at.isoformat(),
            'same_native_bodies':True}))
    native_times=[day0-timedelta(minutes=90),day0-timedelta(minutes=60)]
    def body(request):
        with db.get_forecasts_connection_with_world(write_class='live',blocking=False):pass
        case.body_available.append({'event_time':case.clock[0].isoformat(),'wall_monotonic':time.perf_counter()})
        payload={'SUMMARY':{'RESPONSE_CODE':1},'UNITS':{'air_temp':'Celsius'},'STATION':[{
            'STID':case.city.wu_station,'OBSERVATIONS':{'date_time':[stamp.astimezone(ZoneInfo(case.city.timezone)).strftime('%Y-%m-%dT%H:%M:%S%z') for stamp in native_times],
            'air_temp_set_1':list(case.values[0]),'sea_level_pressure_set_1':[1010,1010]}}]}
        return httpx.Response(200,json=payload)
    with HTTP_CLIENT(transport=httpx.MockTransport(body)) as client:
        monkeypatch.setattr(upstream,'_bounded_body',lambda ignored,*args,**kwargs:native_bounded_body(client,*args,**kwargs))
        case.clock[0]=day0
        if case.params.get('ordinary_reactor'):
            _register_ordinary_reactor(case)
        controls.ledger.refresh(venue.adapter)
        bankroll_provider.warm_from_collateral_snapshot()
        ws_gap_guard.record_message(observed_at=case.clock[0],subscription_state='SUBSCRIBED')
        asyncio.run(controls.supervisor.run_once())
        riskguard.tick()
        from src.data.substrate_observer import refresh_money_path_substrate_now
        substrate=refresh_money_path_substrate_now(
            families=[(case.city.name,str(case.request.target_date),'high')],
            condition_ids=tuple(sorted(set(venue.tokens.values()))),
            reason='scheduled_lineage_day0_family_refresh',force_refresh=True)
        record_property('day0_family_substrate',json.dumps(substrate,default=str))
        assert substrate.get('inserted')==6,substrate
        # Re-anchor native PONG delivery after the explicit simulated jump.
        for item in case.pump.transport_ticks:item[0]=day0+item[1]
        case.pump.transport_ticks.append([day0+timedelta(seconds=1),timedelta(seconds=1),poll])
        case.pump.enable('ingest_day0_noaa_wrh_current')
        case.pump.enable('ingest_current_temperature_delivery')
        case.pump.advance()
        case.pump.run_until(day0+timedelta(seconds=45))
        prior=portfolio.load_runtime_open_portfolio(case.trade).positions[0]
        record_property('observed_day0_probability_snapshots',json.dumps(observed_snapshots,default=str))
        record_property('prior_day0_position',json.dumps({'q':prior.last_monitor_prob,'posts':venue.posts,'state':prior.state}))
        assert len(venue.posts)==1,venue.posts
        if case.params.get('stale_hourly'):
            assert prior.last_monitor_prob_is_fresh is False
            assert observed_snapshots and all(row['bundle'] is None for row in observed_snapshots)
            record_property('stale_hourly_refusal',json.dumps({'captured_at':(case.native_request.computed_at-timedelta(minutes=2)).isoformat(),
                'decision_at':case.clock[0].isoformat(),'stale_probability_consumed':False,'sell_submitted':False}))
            return
        assert prior.last_monitor_prob_is_fresh is True
        assert 0<prior.last_monitor_prob<1
        hold_artifact=json.loads(case.trade.execute("SELECT artifact_json FROM decision_log WHERE mode='exit_monitor' ORDER BY id DESC LIMIT 1").fetchone()[0])
        hold=next(row for row in hold_artifact['monitor_results'] if row['position_id']==case.position.trade_id)
        assert hold['should_exit'] is False and hold['exit_reason']=='HOLD',hold
        record_property('prior_local_position_hold',json.dumps({'scope':'local Position HOLD; global full-family preparation separately required',
            'decision':hold,'at':hold_artifact['completed_at'],
            'global_preparation_requested':hold_artifact['summary'].get('monitor_hold_full_family_preparation_requested')}))
        if case.params.get('capture_fault'):
            _install_unreadable_capture(case,record_property)
        partial_confirmations=[]
        if case.params.get('first_exit_match_cap'):
            delivered=set()
            case.partial_position_trace=[]
            def deliver_partial_matches():
                for post in venue.posts[1:]:
                    if post['order_id'] in delivered or Decimal(post['filled_size'])<=0:
                        continue
                    if case.clock[0]<datetime.fromisoformat(post['at'])+timedelta(seconds=2):
                        continue
                    payload=venue.confirm_trade(order_id=post['order_id'])
                    assert payload['asset_id']==post['token_id'] and payload['side']==post['side']
                    assert payload['market']==post['condition_id'] and 'fee_paid_micro' not in payload
                    result=asyncio.run(stream.handle_raw_message(json.dumps(payload)))
                    terminal=None
                    if Decimal(post['filled_size'])<Decimal(post['size']):
                        terminal={**venue.orders[post['order_id']],'event_type':'order','type':'CANCELLATION',
                            'timestamp':str(int(case.clock[0].timestamp()*1000))}
                        asyncio.run(stream.handle_raw_message(json.dumps(terminal)))
                    partial_confirmations.append({'at':case.clock[0].isoformat(),'payload':payload,
                        'result':result,'terminal_order':terminal,
                        'external_inventory':{key:str(value) for key,value in venue.inventory.items()}})
                    delivered.add(post['order_id'])
                case.partial_position_trace.append({'at':case.clock[0].isoformat(),
                    'position':dict(case.trade.execute('SELECT shares,chain_shares,phase FROM position_current WHERE position_id=?',
                        (case.position.trade_id,)).fetchone())})
            case.pump.transport_ticks.append([case.clock[0]+timedelta(seconds=1),
                timedelta(seconds=1),deliver_partial_matches])
        correction=case.params.get('capture_correction')
        case.values[0]=(26.,24.) if correction=='revision_only' else (25.,24.) if correction=='downward' else (30.,25.)
        case.pump.run_until(day0+timedelta(seconds=120))
        record_property('day0_scheduled_posts',json.dumps(venue.posts))
        record_property('reactor_constructor_deferrals',json.dumps(constructor_deferrals))
        if correction:
            _assert_capture_source_correction(case,record_property,reactor_invocations)
            return
        # Native chain authority can let the scheduled SELL and its MATCHED
        # order-fact projection complete before this observation checkpoint.
        # Read the original canonical history even when it is already closed.
        current=SimpleNamespace(**dict(case.trade.execute(
            'SELECT last_monitor_prob,last_monitor_prob_is_fresh,phase FROM position_current WHERE position_id=?',
            (case.position.trade_id,)).fetchone()))
        assert current.last_monitor_prob_is_fresh==1
        assert 0<current.last_monitor_prob<prior.last_monitor_prob
        latest=observed_snapshots[-1]
        bundle=(latest['binding']or{}).get('day0_causal_evidence_bundle') or latest['bundle']
        vector=(latest['binding']or{}).get('day0_remaining_vector_witness') or latest['vector']
        assert bundle and vector and latest['validation'] and latest['validation']['reason'] is None
        record_property('positive_q_handoff',json.dumps({'before_q':prior.last_monitor_prob,'after_q':current.last_monitor_prob,
            'probability_authority':latest['authority'],'bundle':bundle,'vector':vector,'validation':latest['validation'],
            'posts':venue.posts,'body_available':case.body_available}))
        if case.params.get('probability_handoff_only'):
            return
        if len(venue.posts)==1:
            case.pump.run_until(venue.window_end+timedelta(seconds=1))
        record_property('full_declared_window',json.dumps({'window_end':venue.window_end.isoformat(),
            'observed_until':case.clock[0].isoformat(),'posts':venue.posts,
            'constructor_deferrals':constructor_deferrals,'reactor_invocations':reactor_invocations}))
        if case.params.get('first_exit_match_cap'):
            _assert_capture_partial_closure(case,venue,stream,partial_confirmations,record_property)
            return
        assert len(venue.posts)==2 and venue.posts[-1]['side']=='SELL',venue.posts
        assert datetime.fromisoformat(venue.posts[-1]['at'])<venue.window_end
        if case.params.get('capture_fault'):
            _record_capture_delivery_cut(case,venue,record_property)
        # Preserve the declared late native-confirmation delivery even if
        # additional normal upstream owners enable an earlier legal match.
        case.pump.run_until(day0+timedelta(seconds=152))
        pre_confirmation_position=dict(case.trade.execute(
            'SELECT phase,shares,chain_shares FROM position_current WHERE position_id=?',
            (case.position.trade_id,)).fetchone())
        pre_confirmation_facts=[dict(row) for row in case.trade.execute(
            'SELECT trade_id,state,source,filled_size FROM venue_trade_facts WHERE venue_order_id=?',
            (venue.posts[1]['order_id'],))]
        pre_confirmation_command=case.trade.execute('SELECT state FROM venue_commands WHERE command_id=?',
            (venue.posts[1]['command_id'],)).fetchone()[0]
        record_property('anonymous_matched_before_native_confirmation',json.dumps({
            'at':case.clock[0].isoformat(),'raw_order':venue.orders[venue.posts[1]['order_id']],
            'command_state':pre_confirmation_command,'position':pre_confirmation_position,
            'trade_facts':pre_confirmation_facts}))
        assert not venue.orders[venue.posts[1]['order_id']]['associate_trades']
        assert pre_confirmation_facts==[], 'anonymous order amounts cannot manufacture an economic trade identity'
        assert pre_confirmation_command!='FILLED'
        assert pre_confirmation_position['phase']!='economically_closed'
        confirmed=venue.confirm_trade()
        assert 'fee_paid_micro' not in confirmed
        record_property('native_exit_confirmation',json.dumps({'trade':confirmed,
            'modeled_external_cash':str(venue.cash),'modeled_external_ctf_shares':str(venue.held_shares)}))
        asyncio.run(stream.handle_raw_message(json.dumps(confirmed)))
        if case.params.get('reentry_control'):
            # Preserve the original SELL's explicitly delayed confirmation.
            # Thereafter deliver every actual native match at the declared
            # two-second lag, including lawfully selected earlier BUYs.
            delivered=set()
            confirmations=[]
            def deliver_native_matches():
                for post in venue.posts[2:]:
                    if post['order_id'] in delivered or Decimal(post['filled_size'])<=0:
                        continue
                    if case.clock[0]<datetime.fromisoformat(post['at'])+timedelta(seconds=2):
                        continue
                    payload=venue.confirm_trade(order_id=post['order_id'])
                    assert payload['asset_id']==post['token_id']
                    assert payload['market']==post['condition_id']
                    assert payload['side']==post['side']
                    assert 'fee_paid_micro' not in payload
                    result=asyncio.run(stream.handle_raw_message(json.dumps(payload)))
                    confirmations.append({'at':case.clock[0].isoformat(),'payload':payload,'result':result})
                    delivered.add(post['order_id'])
            case.pump.transport_ticks.append([case.clock[0]+timedelta(seconds=1),
                timedelta(seconds=1),deliver_native_matches])
        case.pump.run_until(case.clock[0]+timedelta(seconds=90))
        row=case.trade.execute('SELECT shares,chain_shares,phase FROM position_current WHERE position_id=?',(case.position.trade_id,)).fetchone()
        record_property('canonical_exit_position',json.dumps(dict(row)))
        assert row['chain_shares']==0 and row['phase']=='economically_closed',dict(row)
        assert not portfolio.load_runtime_open_portfolio(case.trade).positions
        command=case.trade.execute('SELECT state FROM venue_commands WHERE command_id=?',(venue.posts[-1]['command_id'],)).fetchone()
        assert command['state']=='FILLED'
        from src.state.fill_dedup import economic_exit_fills_for_position
        economic=economic_exit_fills_for_position(case.trade,case.position.trade_id)
        from dataclasses import asdict
        record_property('canonical_exit_economic_fills',json.dumps([asdict(fill) for fill in economic],default=str))
        assert sum(fill.quantity for fill in economic)==Decimal('12.5')
        facts=case.trade.execute('SELECT state,filled_size,fill_price,fee_paid_micro,source FROM venue_trade_facts WHERE venue_order_id=?',
            (venue.posts[-1]['order_id'],)).fetchall()
        assert facts and all(row['fee_paid_micro'] is None for row in facts)
        cash_facts=case.trade.execute('SELECT status,reason FROM venue_fill_cash_facts').fetchall()
        assert cash_facts and all(row['status']=='UNKNOWN' for row in cash_facts)
        record_property('exit_native_cash_authority',json.dumps({'trade_facts':[dict(row) for row in facts],
            'cash_facts':[dict(row) for row in cash_facts]}))
        intent=json.loads(case.trade.execute("SELECT payload_json FROM position_events WHERE position_id=? AND event_type='EXIT_INTENT' ORDER BY sequence_no DESC LIMIT 1",
            (case.position.trade_id,)).fetchone()[0])
        receipt=intent['exit_intent_capital_certificate']['global_auction_receipt']
        artifact=json.loads(case.trade.execute('SELECT artifact_json FROM decision_log WHERE id=?',(receipt['decision_log_id'],)).fetchone()[0])
        record_property('actual_sell_intent',json.dumps(intent))
        record_property('actual_sell_global_selection',json.dumps(artifact))
        if case.params.get('capture_fault'):
            _assert_capture_action_attribution(case,venue,intent,artifact,record_property)
            _assert_capture_reset(case,record_property)
        if case.params.get('reentry_control'):
            from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
            recovery=main._start_edli_boot_fill_bridge_recovery()
            if recovery is not None:
                recovery.join(15)
                assert not recovery.is_alive()
            assert main._edli_boot_fill_bridge_recovery_complete.is_set()
            record_property('reentry_boot_admission',json.dumps(main._live_entry_block_scope(),default=str))
            from src.data import source_health_probe
            from src.config import state_path
            monkeypatch.setattr(source_health_probe,'datetime',case.pump.clock_type)
            case.pump.enable('ingest_source_health_probe')
            case.pump.advance()
            record_property('reentry_native_health_probe',state_path('source_health.json').read_text())
            record_property('reentry_health_admission',json.dumps(main._live_entry_block_scope(),default=str))
            def refresh_account_and_book():
                assert venue.window_open()
                controls.ledger.refresh(venue.adapter)
                bankroll_provider.warm_from_collateral_snapshot()
                asyncio.run(controls.supervisor.run_once())
                riskguard.tick()
                return refresh_money_path_substrate_now(
                    families=[(case.city.name,str(case.request.target_date),'high')],
                    condition_ids=tuple(sorted(set(venue.tokens.values()))),
                    reason='scheduled_reentry_control',force_refresh=True)
            case.pump.run_until(day0+timedelta(seconds=300))
            stale_book=refresh_account_and_book()
            _,old=read_current_noaa_wrh_snapshot(case.forecasts,city=case.city,
                target_date=str(case.request.target_date),as_of=case.clock[0])
            case.pump.run_until(day0+timedelta(seconds=390))
            _,replayed=read_current_noaa_wrh_snapshot(case.forecasts,city=case.city,
                target_date=str(case.request.target_date),as_of=case.clock[0])
            assert old.response_sha256==replayed.response_sha256
            reentry_commands=[dict(row) for row in case.trade.execute(
                "SELECT command_id,token_id,side,state,created_at FROM venue_commands WHERE created_at>? ORDER BY created_at",
                (venue.posts[1]['at'],))]
            assert not [row for row in reentry_commands if row['side']=='BUY' and row['token_id']==venue.held_token]
            record_property('reentry_same_evidence',json.dumps({'book':stale_book,
                'source_revision':replayed.response_sha256,'window':[stamp.isoformat() for stamp in venue.restored_window],
                'at':case.clock[0].isoformat(),'posts':venue.posts,'commands':reentry_commands,
                'scope':'same held-token no-repurchase observation; other outcome BUY is allowed by the action law'}))
            from src.control.live_health import _current_global_auction_candidate_payload
            # Bind the first actual later BUY to its immutable certificate and
            # winning cut, without assuming it waits for bid restoration.
            first_buy=next(post for post in venue.posts[2:] if post['side']=='BUY' and Decimal(post['filled_size'])>0)
            attribution=case.trade.execute('SELECT decision_certificate_hash FROM position_decision_attribution WHERE command_id=?',
                (first_buy['command_id'],)).fetchone()
            assert attribution is not None
            with db.get_world_connection_read_only() as world:
                certificate=json.loads(world.execute('SELECT payload_json FROM decision_certificates WHERE certificate_hash=?',
                    (attribution[0],)).fetchone()[0])
            cuts=[dict(row) for row in case.trade.execute("SELECT id,started_at,artifact_json FROM decision_log WHERE mode IN ('global_single_order_auction','global_single_order_auction_delta') ORDER BY id")]
            eligible=[row for row in cuts if json.loads(row['artifact_json'])['summary'].get('winner_candidate_id')==certificate['candidate_id']]
            assert eligible, {'first_buy':first_buy,'candidate_id':certificate['candidate_id']}
            selected=eligible[0]
            assert datetime.fromisoformat(selected['started_at'])<=datetime.fromisoformat(first_buy['at'])
            summary=json.loads(selected['artifact_json'])['summary']
            candidates=_current_global_auction_candidate_payload(case.trade,summary)
            index=[dict(zip(candidates['buy_candidate_index_fields'],values)) for values in candidates['buy_candidate_index']]
            winner=next(row for row in index if row['candidate_id']==summary['winner_candidate_id'])
            same_token_indexes={i for i,row in enumerate(index) if row['token_id']==venue.held_token}
            same_token_rejections=[row for row in candidates['rejected_groups'] if same_token_indexes.intersection(row['candidate_indexes'])]
            record_property('reentry_first_eligible_selection',json.dumps({'decision_log_id':selected['id'],
                'at':selected['started_at'],'winner':winner,'same_token_rejections':same_token_rejections,
                'summary':summary,'source_revision':replayed.response_sha256,
                'actual_post':first_buy,'decision_certificate_hash':attribution[0]}))
            assert venue.posts[2]['token_id']==winner['token_id']
            assert winner['token_id']!=venue.held_token
            assert any(row['reason']=='FAMILY_JOINT_NO_POSITIVE_TARGET' for row in same_token_rejections),same_token_rejections
            case.values[0]=(26.,25.)
            corrected_book=refresh_account_and_book()
            record_property('reentry_observation_endpoint',json.dumps({
                'prior_observation_until':(day0+timedelta(seconds=540)).isoformat(),
                'observation_until':venue.restored_window[1].isoformat(),
                'reason':'cover the already-declared unchanged-evidence tail; no liquidity or source window extension'}))
            case.pump.run_until(venue.restored_window[1])
            _,corrected=read_current_noaa_wrh_snapshot(case.forecasts,city=case.city,
                target_date=str(case.request.target_date),as_of=case.clock[0])
            assert corrected.response_sha256!=replayed.response_sha256
            assert corrected.extreme('high').value==26
            outcomes=[dict(row) for row in case.trade.execute("SELECT id,mode,artifact_json FROM decision_log WHERE started_at>=? ORDER BY id",
                ((day0+timedelta(seconds=300)).isoformat(),))]
            record_property('reentry_corrected_evidence',json.dumps({'book':corrected_book,
                'old_revision':replayed.response_sha256,'new_revision':corrected.response_sha256,
                'source_received_at':corrected.received_at.isoformat(),'at':case.clock[0].isoformat(),
                'posts':venue.posts,'decision_outcomes':outcomes}))
            record_property('reentry_dispatch',json.dumps({'invocations':reactor_invocations,
                'constructor_deferrals':constructor_deferrals}))
            terminal_commands=[dict(row) for row in case.trade.execute(
                "SELECT command_id,token_id,side,state,created_at FROM venue_commands WHERE created_at>? ORDER BY created_at",
                (venue.posts[1]['at'],))]
            record_property('reentry_terminal_commands',json.dumps(terminal_commands))
            unknown=[row for row in terminal_commands if row['state']=='SUBMIT_UNKNOWN_SIDE_EFFECT']
            record_property('reentry_native_confirmations',json.dumps(confirmations))
            record_property('reentry_native_chain_sync',json.dumps(case.chain_sync_calls))
            record_property('reentry_external_account',json.dumps({'inventory':venue.inventory,
                'cash':venue.cash,'confirmations':[row for row in venue.calls if row['operation']=='native_confirmed']},default=str))
            assert not unknown,unknown
            lineage=[]
            for post in venue.posts[2:]:
                command=dict(case.trade.execute('SELECT * FROM venue_commands WHERE command_id=?',(post['command_id'],)).fetchone())
                facts=[dict(row) for row in case.trade.execute('SELECT * FROM venue_trade_facts WHERE venue_order_id=?',(post['order_id'],))]
                attribution=case.trade.execute('SELECT * FROM position_decision_attribution WHERE command_id=?',(post['command_id'],)).fetchone()
                assert command['token_id']==post['token_id'] and command['side']==post['side']
                assert Decimal(post['filled_size'])==0 or command['state']=='FILLED',command
                for fact in facts:
                    payload=json.loads(fact['raw_payload_json'])
                    assert payload['asset_id']==post['token_id'] and payload['market']==post['condition_id']
                    assert payload['side']==post['side'] and fact['command_id']==post['command_id']
                    assert fact['fee_paid_micro'] is None
                if post['side']=='BUY' and Decimal(post['filled_size'])>0:
                    assert attribution and attribution['resolution']=='ATTRIBUTED'
                lineage.append({'post':post,'command':command,'facts':facts,
                    'attribution':dict(attribution) if attribution else None})
            record_property('reentry_actual_lineage',json.dumps(lineage))
            positions=[dict(row) for row in case.trade.execute('SELECT position_id,token_id,no_token_id,direction,phase,shares,chain_shares FROM position_current')]
            record_property('reentry_terminal_positions',json.dumps(positions))
            original=next(row for row in positions if row['position_id']==case.position.trade_id)
            assert original['phase']=='economically_closed' and original['chain_shares']==0
            # A late old fill and receive-handler restart must not reopen the
            # original position or attribute it to any new token/command.
            from src.ingest import polymarket_user_channel as user_channel
            before=[tuple(row) for row in case.trade.execute('SELECT * FROM venue_trade_facts ORDER BY trade_fact_id')]
            commands_before=[tuple(row) for row in case.trade.execute('SELECT * FROM venue_commands ORDER BY command_id')]
            command_events_before=case.trade.execute('SELECT COUNT(*) FROM venue_command_events').fetchone()[0]
            position_events_before=case.trade.execute('SELECT COUNT(*) FROM position_events').fetchone()[0]
            posts_before=len(venue.posts)
            restarted=user_channel.PolymarketUserChannelIngestor(venue.adapter,sorted(set(venue.tokens.values())),
                auth=stream.auth,conn_factory=db.get_trade_connection_with_world)
            for payload in venue.confirmed_trades:
                late={**payload,'timestamp':str(int(case.clock[0].timestamp()))}
                assert asyncio.run(stream.handle_raw_message(json.dumps(late)))['reason']=='duplicate_trade_fact'
                assert asyncio.run(restarted.handle_raw_message(json.dumps(late)))['reason']=='duplicate_trade_fact'
            assert before==[tuple(row) for row in case.trade.execute('SELECT * FROM venue_trade_facts ORDER BY trade_fact_id')]
            assert commands_before==[tuple(row) for row in case.trade.execute('SELECT * FROM venue_commands ORDER BY command_id')]
            assert command_events_before==case.trade.execute('SELECT COUNT(*) FROM venue_command_events').fetchone()[0]
            assert position_events_before==case.trade.execute('SELECT COUNT(*) FROM position_events').fetchone()[0]
            assert posts_before==len(venue.posts)
            assert positions==[dict(row) for row in case.trade.execute('SELECT position_id,token_id,no_token_id,direction,phase,shares,chain_shares FROM position_current')]
            cash_facts=[dict(row) for row in case.trade.execute('SELECT status,reason FROM venue_fill_cash_facts')]
            assert cash_facts and all(row['status']=='UNKNOWN' for row in cash_facts)
            record_property('reentry_duplicate_restart',json.dumps({'all_confirmed_trades_replayed':len(venue.confirmed_trades),
                'trade_facts_unchanged':True,'positions_unchanged':True,'commands_unchanged':True,
                'command_event_count_unchanged':True,'position_event_count_unchanged':True,
                'post_count_unchanged':True,'cash_facts':cash_facts,
                'scope':'receive-handler memory restart; no daemon restart claimed'}))
            # A filled ENTRY must hand its immutable probability authority to
            # held monitoring. An unavailable binding is a runtime defect, not
            # a lawful economic reason to report corrected-evidence no-trade.
            from src.calibration.market_anchored_live_fit import load_held_entry_calibration
            binding_results=[]
            with db.get_world_connection_read_only() as world:
                for row in lineage:
                    if row['post']['side']!='BUY' or Decimal(row['post']['filled_size'])<=0:
                        continue
                    attributed=row['attribution']
                    position=next(item for item in positions if item['position_id']==attributed['position_id'])
                    side='YES' if position['direction']=='buy_yes' else 'NO'
                    assert position['token_id' if side=='YES' else 'no_token_id']==row['post']['token_id']
                    certificate=dict(world.execute('SELECT certificate_hash,payload_json FROM decision_certificates WHERE certificate_hash=?',
                        (attributed['decision_certificate_hash'],)).fetchone())
                    result={'position_id':position['position_id'],'token_id':row['post']['token_id'],
                        'command_id':row['post']['command_id'],'certificate':certificate}
                    try:
                        binding=load_held_entry_calibration(case.trade,position_id=position['position_id'],
                            token_id=row['post']['token_id'],side=side,world_conn=world)
                        result['binding_type']=type(binding).__name__
                    except ValueError as exc:
                        result['error']=str(exc)
                    binding_results.append(result)
            record_property('reentry_held_probability_handoff',json.dumps(binding_results))
            corrected_cuts=[]
            for row in outcomes:
                artifact=json.loads(row['artifact_json'])
                if row['mode'] not in {'global_single_order_auction','global_single_order_auction_delta'}:
                    continue
                if datetime.fromisoformat(artifact['started_at'])<corrected.received_at:
                    continue
                summary=artifact['summary']
                corrected_cuts.append({'decision_log_id':row['id'],'at':artifact['started_at'],
                    'winner_candidate_id':summary.get('winner_candidate_id'),
                    'no_trade_reason':summary.get('no_trade_reason'),
                    'candidates':_current_global_auction_candidate_payload(case.trade,summary)})
            record_property('reentry_corrected_disposition',json.dumps(corrected_cuts))
            assert corrected_cuts, 'changed native source must reach a normal decision cut'
            assert binding_results and all('error' not in row for row in binding_results),binding_results
            _assert_reentry_finality(case,venue,lineage,positions,outcomes,corrected,
                corrected_cuts,record_property)


def _assert_reentry_finality(case,venue,lineage,positions,outcomes,corrected,corrected_cuts,record_property):
    """Trace the naturally selected actions through their exact native fills."""
    from src.control.live_health import _current_global_auction_candidate_payload
    from src.state.fill_dedup import economic_exit_fills_for_position
    first_buy=lineage[0]
    assert first_buy['post']['side']=='BUY'
    alternate=first_buy['post']['token_id']
    alternate_position=first_buy['attribution']['position_id']
    assert alternate!=venue.held_token
    monitors=[]
    for row in outcomes:
        if row['mode']!='exit_monitor':continue
        artifact=json.loads(row['artifact_json'])
        monitors.extend({'at':artifact['started_at'],**result} for result in artifact.get('monitor_results',[])
            if result['position_id']==alternate_position)
    before=[row for row in monitors if datetime.fromisoformat(row['at'])<corrected.received_at]
    assert any(row['fresh_prob']==1 and not row['should_exit']
        and row['exit_reason'].startswith('DAY0_HARD_FACT_STRUCTURAL_WIN_HOLD') for row in before),before
    selected_sells=[candidate for cut in corrected_cuts for candidate in cut['candidates']['detailed']
        if candidate['action']=='SELL' and candidate['status']=='SELECTED'
        and candidate['position_id']==alternate_position]
    assert selected_sells, 'the corrected lawful SELL must reach its normal global selection'
    exits=[row for row in lineage if row['post']['side']=='SELL'
        and row['command']['position_id']==alternate_position]
    assert exits, 'selected corrected SELL must persist and submit, not stop at preflight'
    exit_proofs=[]
    for row in exits:
        post,command=row['post'],row['command']
        intent_row=case.trade.execute("SELECT payload_json FROM position_events WHERE position_id=? AND event_type='EXIT_INTENT' AND occurred_at<=? ORDER BY sequence_no DESC LIMIT 1",
            (alternate_position,post['at'])).fetchone()
        assert intent_row is not None
        intent=json.loads(intent_row[0])
        capital=intent['exit_intent_capital_certificate']
        probability=intent['exit_intent_probability_receipt']
        receipt=capital['global_auction_receipt']
        artifact=json.loads(case.trade.execute('SELECT artifact_json FROM decision_log WHERE id=?',
            (receipt['decision_log_id'],)).fetchone()[0])
        summary=artifact['summary']
        candidates=_current_global_auction_candidate_payload(case.trade,summary)
        selected=next(candidate for candidate in candidates['detailed']
            if candidate['candidate_id']==receipt['winner_candidate_id'])
        assert selected['status']=='SELECTED' and selected['action']=='SELL'
        assert selected['token_id']==capital['token_id']==post['token_id']==alternate
        assert selected['position_id']==capital['position_id']==command['position_id']==alternate_position
        assert capital['condition_id']==post['condition_id']==venue.tokens[alternate]
        assert capital['candidate_id']==receipt['winner_candidate_id']==summary['winner_candidate_id']
        assert capital['actuation_identity']==receipt['winner_actuation_identity']==summary['winner_actuation_identity']
        assert receipt['receipt_hash']==summary['receipt_hash']
        assert receipt['execution_binding_hash']==summary['execution_binding_hash']
        assert Decimal(capital['selected_shares'])==Decimal(post['size'])==Decimal(str(command['size']))
        assert Decimal(capital['exact_limit_price'])==Decimal(post['price'])
        assert capital['expected_sell_delta_log_wealth']>0 and capital['expected_sell_ev_usd']>0
        assert intent['exit_intent_fresh_prob_is_fresh'] is True
        q=probability['held_side_probability']
        assert 0<q<1
        assert q==capital['held_probability_mean']==capital['held_probability_point']==selected['q_served']
        assert q==intent['exit_intent_fresh_prob']
        assert probability['probability_functional']==capital['sell_probability_functional']=='POSTERIOR_PREDICTIVE_MEAN'
        for key in ('probability_witness_identity','probability_content_identity','source_truth_identity','q_version'):
            assert probability[key] and probability[key]==capital[key]
        assert selected['probability_witness_identity']==probability['probability_witness_identity']
        correction=probability['payoff_q_correction']
        assert correction['q_corrected']==q
        for key in ('probability_witness_identity','probability_content_identity','source_truth_identity','q_version'):
            assert correction[key]==probability[key]
        assert probability['q_version'].startswith('day0-semrev:')
        native=[fact for fact in row['facts'] if fact['state']=='CONFIRMED' and fact['source']=='WS_USER']
        assert len(native)==1 and Decimal(native[0]['filled_size'])==Decimal(post['filled_size'])
        fills=economic_exit_fills_for_position(case.trade,alternate_position,venue_order_id=post['order_id'])
        assert len(fills)==1 and fills[0].trade_id==native[0]['trade_id']
        assert fills[0].quantity==Decimal(post['filled_size'])
        assert fills[0].notional==Decimal(post['filled_size'])*Decimal(post['fill_price'])
        exit_proofs.append({'post':post,'intent':intent,'selection':selected,'native_fact':native[0]})
    alternate_final=next(row for row in positions if row['position_id']==alternate_position)
    bought=sum(Decimal(row['post']['filled_size']) for row in lineage
        if row['post']['side']=='BUY' and row['command']['position_id']==alternate_position)
    sold=sum(Decimal(row['post']['filled_size']) for row in exits)
    assert sold==bought, 'this naturally selected full exit must leave no canonical residual'
    assert alternate_final['phase']=='economically_closed' and alternate_final['chain_shares']==0
    assert venue.inventory[alternate]==0
    assert not [row for row in lineage if row['post']['side']=='BUY' and row['post']['token_id']==alternate
        and datetime.fromisoformat(row['post']['at'])>=corrected.received_at], 'no stale alternate-token rebuy after corrected source'
    reentries=[row for row in lineage if row['post']['side']=='BUY' and row['post']['token_id']==venue.held_token]
    assert reentries, 'the corrected original-token winner must complete native reentry'
    for row in reentries:
        assert datetime.fromisoformat(row['post']['at'])>=corrected.received_at
        assert row['attribution']['position_id']!=case.position.trade_id
        assert row['command']['position_id']==row['attribution']['position_id']
        assert row['post']['command_id'] not in {venue.posts[0]['command_id'],venue.posts[1]['command_id']}
        assert len([fact for fact in row['facts'] if fact['state']=='CONFIRMED' and fact['source']=='WS_USER'])==1
    original=next(row for row in positions if row['position_id']==case.position.trade_id)
    assert original['phase']=='economically_closed' and original['shares']==float(venue.posts[0]['filled_size'])
    assert original['chain_shares']==0
    initial_attribution=case.trade.execute('SELECT position_id FROM position_decision_attribution WHERE command_id=?',
        (venue.posts[0]['command_id'],)).fetchone()[0]
    assert initial_attribution==case.position.trade_id
    active_inventory={token:Decimal(0) for token in venue.tokens}
    for row in positions:
        if row['phase'] in {'economically_closed','settled','voided','admin_closed'}:continue
        token=row['token_id'] if row['direction']=='buy_yes' else row['no_token_id']
        active_inventory[token]+=Decimal(str(row['shares']))
    assert active_inventory==venue.inventory
    assert len({post['order_id'] for post in venue.posts})==len(venue.posts)
    assert len({post['command_id'] for post in venue.posts})==len(venue.posts)
    assert case.trade.execute('SELECT COUNT(*) FROM venue_commands').fetchone()[0]==len(venue.posts)
    record_property('reentry_final_native_acceptance',json.dumps({'alternate_position':alternate_position,
        'monitors':monitors,'exit_proofs':exit_proofs,'corrected_reentries':reentries,
        'old_position':original,'active_inventory':active_inventory,
        'observed_until':case.clock[0].isoformat(),'restored_window_end':venue.restored_window[1].isoformat()},default=str))
