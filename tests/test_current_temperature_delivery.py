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
