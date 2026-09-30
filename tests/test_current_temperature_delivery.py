# Created: 2026-09-29
# Last reused/audited: 2026-09-29
# Authority: REQ-20260929-223929-bf51a2; K1/INV-37 and causal source revision delivery.
"""Source revision delivery is not conditional on an incumbent carrier."""
from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace
import pytest
from src.data import replacement_fusion_upgrade_trigger as fusion
UTC=timezone.utc

@pytest.mark.parametrize("incumbent,carrier", [(False,False),(True,False),(True,True)])
@pytest.mark.parametrize("target", ["2026-09-29","2026-09-28"])
def test_current_revision_reaches_every_incumbent_shape(monkeypatch,incumbent,carrier,target):
    now=datetime(2026,9,29,12,tzinfo=UTC)
    cycle="2026-09-29T06:00:00+00:00"
    current={"source":"fmi_airport_temperature","observed_at_utc":target+"T09:20:00+00:00","value_native":15.6}
    consumed=[None]
    monkeypatch.setattr(fusion,"_latest_posterior_inputs",lambda *_a,**_k:(
        cycle if incumbent else None,frozenset(),{},frozenset(),frozenset(),None,(),False,False,consumed[0],carrier,False,{}))
    monkeypatch.setattr(fusion,"_capturable_current_temperature_state",lambda **_:current)
    monkeypatch.setattr(fusion,"_capturable_inputs_for_scope",lambda *_a,**_k:{})
    monkeypatch.setattr("src.data.replacement_input_hwm.latest_eligible_ensemble_input_cycle",lambda *_a,**_k:datetime.fromisoformat(cycle))
    conn=sqlite3.connect(":memory:")
    verdict=fusion.scope_capture_offers_larger_provider_set(conn,city="Helsinki",target_date=target,metric="high",decision_time=now,changed_sources=("day0_current_temperature_state",))
    assert verdict["is_upgrade"]
    assert verdict["source_cycle_time"]==cycle
    assert verdict["changed_input_revisions"]["day0_current_temperature_state"]==current
    if incumbent:
        consumed[0]=current
        assert not fusion.scope_capture_offers_larger_provider_set(conn,city="Helsinki",target_date=target,metric="high",decision_time=now,changed_sources=("day0_current_temperature_state",))["is_upgrade"]
    conn.close()

def test_missing_ensemble_is_not_a_fabricated_first_posterior(monkeypatch):
    monkeypatch.setattr(fusion,"_latest_posterior_inputs",lambda *_a,**_k:(None,frozenset(),{},frozenset(),frozenset(),None,(),False,False,None,False,False,{}))
    monkeypatch.setattr(fusion,"_capturable_current_temperature_state",lambda **_:{"source":"fixture","value_native":15.6})
    monkeypatch.setattr("src.data.replacement_input_hwm.latest_eligible_ensemble_input_cycle",lambda *_a,**_k:None)
    with sqlite3.connect(":memory:") as conn:
        result=fusion.scope_capture_offers_larger_provider_set(conn,city="Helsinki",target_date="2026-09-29",metric="high",decision_time=datetime(2026,9,29,12,tzinfo=UTC))
    assert not result["is_upgrade"]
    assert result["source_cycle_time"] is None


def test_current_and_ended_held_scopes_survive_calendar_rollover():
    from src.data.physical_current_delivery import current_temperature_delivery_scopes
    city=SimpleNamespace(name="fixture",timezone="Asia/Tokyo")
    held={("fixture","2026-09-28","high"):0,("foreign","2026-09-28","high"):0}
    result=current_temperature_delivery_scopes((city,),now=datetime(2026,9,29,23,tzinfo=UTC),held=held)
    assert set(result)=={("fixture","2026-09-28","high"),("fixture","2026-09-30","high"),("fixture","2026-09-30","low")}


def test_replay_does_not_require_new_http_or_inprocess_wake(monkeypatch):
    from src.data import physical_current_delivery as delivery
    from src.data import replacement_forecast_production as production
    monkeypatch.setattr("src.data.replacement_forecast_seed_discovery.held_position_family_priorities",lambda:{})
    calls=[]
    monkeypatch.setattr(production,"_enqueue_fusion_upgrade_reseeds_if_needed",lambda cfg,**kw:calls.append(kw) or {"status":"FUSION_UPGRADE_TRIGGER"})
    city=SimpleNamespace(name="fixture",timezone="UTC")
    for _ in range(2):
        delivery.reconcile_current_temperature_delivery({},cities=(city,),now=datetime(2026,9,29,12,tzinfo=UTC))
    assert len(calls)==2
    assert calls[0]["scopes"]==calls[1]["scopes"]
    assert calls[1]["changed_sources"]==("day0_current_temperature_state",)


def test_unprojected_ended_day_rest_remains_in_delivery_scope(monkeypatch, tmp_path):
    from src.data import physical_current_delivery as delivery
    from src.state import db
    trade_path, forecast_path = tmp_path/'trade.db', tmp_path/'forecast.db'
    with sqlite3.connect(trade_path) as trade:
        trade.executescript("""
        CREATE TABLE venue_commands(command_id,position_id,venue_order_id,state,token_id,snapshot_id,intent_kind);
        CREATE TABLE venue_order_facts(venue_order_id,state,remaining_size,local_sequence);
        CREATE TABLE position_current(position_id,city,target_date,temperature_metric,condition_id,phase);
        CREATE TABLE executable_market_snapshots(snapshot_id,condition_id,selected_outcome_token_id,captured_at);
        INSERT INTO venue_commands VALUES('command','not-projected','venue-order','ACKED','token','snapshot','ENTRY');
        INSERT INTO venue_order_facts VALUES('venue-order','LIVE',10,1);
        INSERT INTO executable_market_snapshots VALUES('snapshot','condition','token','2026-09-29T12:00:00Z');
        """)
    with sqlite3.connect(forecast_path) as forecasts:
        forecasts.execute('CREATE TABLE market_events(condition_id,city,target_date,temperature_metric)')
        forecasts.execute("INSERT INTO market_events VALUES('condition','fixture','2026-09-28','low')")
    monkeypatch.setattr('src.data.replacement_forecast_seed_discovery.held_position_family_priorities', lambda: {})
    monkeypatch.setattr(db,'get_trade_connection_read_only', lambda: sqlite3.connect(f'file:{trade_path}?mode=ro',uri=True))
    monkeypatch.setattr(db,'get_forecasts_connection_read_only', lambda: sqlite3.connect(f'file:{forecast_path}?mode=ro',uri=True))
    held=delivery.current_temperature_priority_families()
    assert held == {('fixture','2026-09-28','low'): 1}
    scopes=delivery.current_temperature_delivery_scopes(
        (SimpleNamespace(name='fixture',timezone='UTC'),),
        now=datetime(2026,9,30,12,tzinfo=UTC), held=held,
    )
    assert ('fixture','2026-09-28','low') in scopes
    with sqlite3.connect(trade_path) as trade:
        assert trade.execute('SELECT COUNT(*) FROM position_current').fetchone()[0] == 0
        trade.execute("INSERT INTO venue_order_facts VALUES('venue-order','CANCELED',0,2)")
    assert delivery.current_temperature_priority_families() == {}


def test_rest_scope_failure_preserves_known_held_progress(monkeypatch, caplog):
    from src.data import physical_current_delivery as delivery
    monkeypatch.setattr('src.data.replacement_forecast_seed_discovery.held_position_family_priorities',
                        lambda: {('fixture','2026-09-28','high'): 0})
    def unavailable():
        raise sqlite3.OperationalError('test unavailable')
    monkeypatch.setattr('src.state.db.get_trade_connection_read_only', unavailable)
    assert delivery.current_temperature_priority_families() == {('fixture','2026-09-28','high'): 0}
    assert 'CURRENT_TEMPERATURE_REST_SCOPE_UNAVAILABLE' in caplog.text
