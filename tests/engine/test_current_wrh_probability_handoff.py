# Created: 2026-10-08
# Last reused/audited: 2026-10-08
# Authority basis: current native WRH source role, causal identity and ENTRY/HELD/JIT parity.
"""Current native WRH authority must survive the scheduled auction and JIT."""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.engine import test_scheduled_statistical_exit_replay as replay
from tests.engine.test_scheduled_statistical_exit_replay import scheduled_source  # noqa: F401
from tests.test_replacement_forecast_materializer import (  # noqa: F401
    _hko_native_surfaces,
    _hko_source_surface,
)


@pytest.mark.parametrize("scheduled_source", [{
    "forecast_inputs": True,
    "cash": 20,
    "full_day0": True,
    "ordinary_reactor": True,
}], indirect=True)
def test_current_wrh_remaining_day_authority_reaches_final_sell(
    scheduled_source, monkeypatch, record_property,
):
    from src.events.day0_authority import (
        DAY0_PROBABILITY_SEMANTICS_REVISION,
        day0_probability_semantics_revision,
    )

    # The existing scheduled replay owns all native input, account, book,
    # scheduler, fill and lifecycle facts. This test adds only the end-to-end
    # probability-role obligation; it supplies no replacement q or proof.
    replay.test_pre_day0_normal_entry_with_declared_account_cash(
        scheduled_source, monkeypatch, record_property,
    )
    row = scheduled_source.trade.execute(
        "SELECT payload_json FROM position_events WHERE position_id=? "
        "AND event_type='EXIT_INTENT' ORDER BY sequence_no DESC LIMIT 1",
        (scheduled_source.position.trade_id,),
    ).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    capital = payload["exit_intent_capital_certificate"]
    probability = payload["exit_intent_probability_receipt"]
    record_property("final_sell_probability_authority", json.dumps({
        "capital": capital,
        "probability": probability,
        "day0_active": payload["exit_intent_day0_active"],
    }))
    assert capital["sell_exit_authority_reason"] != "non_day0_family"
    assert day0_probability_semantics_revision(capital["q_version"]) == (
        DAY0_PROBABILITY_SEMANTICS_REVISION
    )
    assert probability["q_version"] == capital["q_version"]
    assert probability["probability_witness_identity"] == capital["probability_witness_identity"]
    assert probability["held_side_probability"] == capital["held_probability_point"]
    assert 0.0 < capital["held_probability_point"] < 1.0


@pytest.fixture
def current_wrh_source(tmp_path, monkeypatch, request):
    source = replay.source.scheduled_source.__wrapped__(tmp_path, monkeypatch, request)
    case = next(source)
    try:
        case.pump.enable("ingest_day0_noaa_wrh_current")
        case.pump.advance()
        yield case
    finally:
        source.close()


def _forecast_trigger(case, *, metric="high", city=None, target_date=None):
    from src.events.opportunity_event import make_opportunity_event

    stamp = case.clock[0].isoformat()
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY", entity_key="synthetic-trigger",
        source="native-source-handoff-test", observed_at=stamp,
        available_at=stamp, received_at=stamp, causal_snapshot_id="trigger-original",
        payload={"city": city or case.city.name,
                 "target_date": target_date or str(case.request.target_date),
                 "metric": metric},
    )


@pytest.mark.parametrize("metric,expected", (("high", 32.0), ("low", 28.0)))
def test_native_current_carrier_preserves_scope_clocks_and_trigger(
    current_wrh_source, metric, expected,
):
    from src.engine.current_day0_observation import current_wrh_probability_event
    from src.events.day0_authority import DAY0_PROVISIONAL_CURRENT_SNAPSHOT

    case = current_wrh_source
    trigger = _forecast_trigger(case, metric=metric)
    original = trigger
    before = case.forecasts.execute("SELECT COUNT(*) FROM world.opportunity_events").fetchone()[0]
    current = current_wrh_probability_event(case.forecasts, trigger, decision_time=case.clock[0])
    payload = json.loads(current.payload_json)
    assert current is not trigger and current.event_id != trigger.event_id
    assert trigger == original and trigger.event_type == "FORECAST_SNAPSHOT_READY"
    assert payload["metric"] == metric and payload["raw_value"] == expected
    assert payload["evidence_finality"] == DAY0_PROVISIONAL_CURRENT_SNAPSHOT
    assert payload["station_id"] == case.city.wu_station
    assert current.causal_snapshot_id == "current_wrh_product:" + payload["raw_report_identity"]
    assert current.available_at == payload["observation_available_at"]
    assert current.available_at <= case.clock[0].isoformat()
    assert case.forecasts.execute("SELECT COUNT(*) FROM world.opportunity_events").fetchone()[0] == before


@pytest.mark.parametrize("values", ((32.0, 27.0), (31.0, 28.0), (28.0,)),
                         ids=("same_extreme_new_body", "correction", "retraction"))
def test_current_source_revision_replaces_read_only_probability_carrier(current_wrh_source, values):
    from src.contracts.exceptions import ObservationUnavailableError
    from src.engine.current_day0_observation import (
        current_wrh_probability_event, current_wrh_probability_replay_event,
    )

    case = current_wrh_source
    trigger = _forecast_trigger(case)
    first = current_wrh_probability_event(case.forecasts, trigger, decision_time=case.clock[0])
    first_at = case.clock[0]
    case.values[0] = values
    case.pump.advance(66)
    current = current_wrh_probability_event(case.forecasts, trigger, decision_time=case.clock[0])
    assert current.causal_snapshot_id != first.causal_snapshot_id
    assert json.loads(current.payload_json)["raw_value"] == max(values)
    assert current.available_at > first.available_at
    # A frozen selection cut can still replay its actual old native receipt;
    # a current final-action read receives the corrected/retracted source.
    historical = current_wrh_probability_event(case.forecasts, trigger, decision_time=first_at)
    assert historical.causal_snapshot_id == first.causal_snapshot_id
    with pytest.raises(ObservationUnavailableError, match="WRH_CURRENT_PROBABILITY_REVISION_SUPERSEDED"):
        current_wrh_probability_replay_event(
            case.forecasts, trigger, selected_at=first_at, decision_time=case.clock[0],
        )


@pytest.mark.parametrize("fault", ("empty", "unit", "station", "proof", "unreadable"))
def test_owned_invalid_current_product_cannot_resurrect_forecast(current_wrh_source, fault):
    from src.contracts.exceptions import ObservationUnavailableError
    from src.engine.current_day0_observation import current_wrh_probability_event

    case = current_wrh_source
    trigger = _forecast_trigger(case)
    if fault == "empty":
        case.values[0] = ()
        case.pump.advance(66)
    elif fault == "unreadable":
        case.forecasts.set_authorizer(lambda action, name, *_rest:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and name == "observations"
            else sqlite3.SQLITE_OK)
    else:
        column, value = {"unit": ("unit", "F"), "station": ("station_id", "WRONG"),
                         "proof": ("high_provenance_metadata", "{")}[fault]
        case.forecasts.execute(f"UPDATE observations SET {column}=? WHERE city=?", (value, case.city.name))
        case.forecasts.commit()
    try:
        with pytest.raises(ObservationUnavailableError, match="WRH_CURRENT_SNAPSHOT_UNAVAILABLE"):
            current_wrh_probability_event(case.forecasts, trigger, decision_time=case.clock[0])
    finally:
        case.forecasts.set_authorizer(None)


def test_future_or_unrelated_family_keeps_its_own_trigger(current_wrh_source, monkeypatch):
    from src import config
    from src.engine.current_day0_observation import current_wrh_probability_event

    case = current_wrh_source
    hong_kong = config.cities_by_name["Hong Kong"]
    monkeypatch.setattr(config, "runtime_cities_by_name", lambda: {
        case.city.name: case.city, hong_kong.name: hong_kong,
    })
    for trigger in (
        _forecast_trigger(case, city=hong_kong.name),
        _forecast_trigger(case, target_date=(case.request.target_date + timedelta(days=1)).isoformat()),
    ):
        assert current_wrh_probability_event(
            case.forecasts, trigger, decision_time=case.clock[0],
        ) is trigger


def test_first_causal_observation_changes_domain_without_rewriting_trigger(tmp_path, monkeypatch, request):
    from src.engine.current_day0_observation import current_wrh_probability_event

    source = replay.source.scheduled_source.__wrapped__(tmp_path, monkeypatch, request)
    case = next(source)
    try:
        trigger = _forecast_trigger(case)
        assert current_wrh_probability_event(
            case.forecasts, trigger, decision_time=case.clock[0],
        ) is trigger
        case.pump.enable("ingest_day0_noaa_wrh_current")
        case.pump.advance()
        current = current_wrh_probability_event(case.forecasts, trigger, decision_time=case.clock[0])
        assert current.event_type == "DAY0_EXTREME_UPDATED"
        assert trigger.event_type == "FORECAST_SNAPSHOT_READY"
    finally:
        source.close()


@pytest.mark.parametrize("values", ((32.0, 27.0), (31.0, 28.0)),
                         ids=("same_high_new_body", "corrected_high"))
@pytest.mark.parametrize("carrier_kind", ("current_view", "legacy_day0"))
def test_final_jit_refuses_changed_native_source_before_probability_replay(
    current_wrh_source, monkeypatch, values, carrier_kind,
):
    from src.contracts.exceptions import ObservationUnavailableError
    from src.contracts.executable_cost_curve import BookLevel, FeeModel
    from src.engine import event_reactor_adapter as adapter
    from src.engine.current_day0_observation import current_wrh_probability_event
    from src.solve.solver import ExecutableSellCurve, GlobalSingleOrderSellCandidate

    case = current_wrh_source
    selected_at = case.clock[0] + timedelta(seconds=50)
    selected_event = current_wrh_probability_event(
        case.forecasts, _forecast_trigger(case), decision_time=selected_at,
    )
    if carrier_kind == "legacy_day0":
        from src.events.opportunity_event import make_opportunity_event

        payload = json.loads(selected_event.payload_json)
        payload.pop("observation_transport")
        selected_event = make_opportunity_event(
            event_type="DAY0_EXTREME_UPDATED", entity_key=selected_event.entity_key,
            source="legacy-day0-trigger", observed_at=selected_event.observed_at,
            available_at=selected_event.available_at, received_at=selected_event.received_at,
            payload=payload, causal_snapshot_id="legacy-day0-trigger-snapshot",
        )
    curve = ExecutableSellCurve(
        token_id="held-token", side="YES", snapshot_id="selected-book", book_hash="selected-book-hash",
        levels=(BookLevel(price=Decimal(".21"), size=Decimal("1")),),
        fee_model=FeeModel(fee_rate=Decimal("0")), min_tick=Decimal(".01"),
        min_order_size=Decimal("1"), quote_ttl=timedelta(seconds=30),
    )
    candidate = GlobalSingleOrderSellCandidate(
        candidate_id="selected-sell", family_key="selected-family", bin_id="selected-bin",
        condition_id="selected-condition", side="YES", token_id="held-token", position_id="selected-position",
        held_shares=Decimal("1"), probability_witness_identity="selected-witness",
        book_snapshot_id=curve.snapshot_id, book_captured_at_utc=selected_at,
        execution_curve_identity="selected-curve", ledger_snapshot_id="selected-ledger",
        executable_sell_curve=curve, resolution_identity="selected-resolution",
        proposal_sell_curve=curve, fill_probability=1.0, fill_probability_source="immediate_taker",
        rest_deadline_minutes=None, neg_risk=False, execution_mode="TAKER_LIMIT",
        probability_functional="POSTERIOR_PREDICTIVE_MEAN",
    )
    case.values[0] = values
    case.pump.advance(66)
    assert case.clock[0] - selected_at < curve.quote_ttl
    monkeypatch.setattr(adapter, "_prepare_current_global_probability_family",
        lambda *_a, **_k: pytest.fail("changed source must reject before any selected-q replay"))
    with pytest.raises(ObservationUnavailableError, match="WRH_CURRENT_PROBABILITY_REVISION_SUPERSEDED"):
        adapter._current_global_actuation_prepared_family(
            selected_event,
            global_actuation=SimpleNamespace(
                probability_witness=SimpleNamespace(captured_at_utc=selected_at),
                decision=SimpleNamespace(candidate=candidate),
            ),
            forecast_conn=case.forecasts, topology_conn=case.forecasts,
            observation_conn=case.forecasts, decision_time=case.clock[0],
        )
