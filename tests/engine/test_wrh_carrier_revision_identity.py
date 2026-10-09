# Created: 2026-10-07
# Last reused/audited: 2026-10-07
# Authority basis: persisted WRH carrier revision identity repair, isolated cloud.
"""Qualified WRH membership survives persisted and fresh carrier boundaries.

Native synthetic bodies use the real current-product writer and reader. The
focused fast-tail carrier uses the real ledger, likelihood, builder, bundle
replay and adapter replay. Forecast vectors and projected decision facts are
bounded synthetic inputs, not a native-GRIB posterior proof; no source or
probability guard is mocked.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import json
import sqlite3

import numpy as np
import pytest

from tests.engine.test_physical_wrh_q_delivery import (  # noqa: F401: shared pytest fixtures
    _write_product, wrh_case, _hko_source_surface, _hko_native_surfaces,
)


def _fast_tail_case(conn, city, now, *, metric="high", legacy=False):
    target = now.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    from src.config import ensemble_n_mc
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.data.day0_hourly_vectors import build_day0_remaining_probability_carrier, day0_remaining_carrier_identity_inputs
    from src.data.day0_fast_obs import build_fast_station_residual_likelihood
    from src.data.replacement_forecast_materializer import _apply_fast_residual_likelihood_to_probability_carrier
    from src.engine import event_reactor_adapter as adapter
    from src.events.day0_authority import DAY0_LIVE_AUTHORITY_MATCHES, DAY0_REMAINING_CENTER_POLICY, DAY0_PROBABILITY_MIXTURE_POLICY
    from src.signal.ensemble_signal import sigma_instrument_for_city
    from src.state.schema.observation_prints_schema import ensure_table, append_print
    semantics = SettlementSemantics.for_city(city)
    world_path = next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "world")
    with sqlite3.connect(world_path) as prints:
        ensure_table(prints)
        for i in range(20):
            at = now - timedelta(minutes=(i+1)*5)
            for channel, value in (("aviationweather_metar", 26.), ("noaa_wrh_zspd", 25.)):
                append_print(prints, city=city.name, station_id="ZSPD", source_channel=channel,
                    publish_ts_utc=at.isoformat(), value_native=value, unit="C", fetched_at_utc=at.isoformat(),
                    raw_report=f"METAR ZSPD {at:%d%H%M}Z 00000KT CAVOK 26/20 Q1010")
        append_print(prints, city=city.name, station_id="ZSPD", source_channel="aviationweather_metar",
            publish_ts_utc=now.isoformat(), value_native=26., unit="C", fetched_at_utc=now.isoformat(),
            raw_report=f"METAR ZSPD {now:%d%H%M}Z 00000KT CAVOK 26/20 Q1010")
    residual = build_fast_station_residual_likelihood(conn, city=city.name, target_date=target,
        metric=metric, observed_source="wu_api+same_station_fast_tail", observation_time=now, decision_time=now)
    assert residual is not None
    likelihood = residual.as_payload()
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    state = read_day0_current_temperature_state(conn=conn, city=city,
        target_date=target, decision_time=now).identity()
    if legacy:
        state.pop("source_revision_identity")
    identity = day0_remaining_carrier_identity_inputs(city=city.name, unit="C",
        decision_time_utc=now.isoformat(), station_id="ZSPD", preliminary_survival_identity=likelihood["identity_hash"])
    identity.update(current_path_state=state, day0_remaining_center_policy=DAY0_REMAINING_CENTER_POLICY,
        day0_probability_mixture_policy=DAY0_PROBABILITY_MIXTURE_POLICY)
    native_bounds = ((None, 28.), (29., 29.), (30., None))
    carrier = build_day0_remaining_probability_carrier(future_extremes_c=(28., 30.),
        boundary_scenarios=((None, 1.),),
        metric=metric, path_error_sigma_c=1., instrument_sigma_c=sigma_instrument_for_city(city).value,
        bin_bounds_c=native_bounds, n_point=ensemble_n_mc(), n_samples=500,
        settlement_semantics=semantics, identity_inputs=identity)
    fact = dict(observation_time=now.isoformat(), observation_available_at=now.isoformat(),
        observed_extreme_native=25., sample_count=2, unit="C", station_id="ZSPD",
        observation_source="noaa_wrh_zspd", raw_payload_sha256=state.get("source_revision_identity", "a" * 64))
    witness = dict(vector_id="synthetic-vector", expected_models=["ifs"], actual_models=["ifs"],
        capture_times_by_model_utc={"ifs": now.isoformat()}, provider_source_cycle_time_by_model_utc={"ifs": now.isoformat()},
        provider_source_available_at_by_model_utc={"ifs": now.isoformat()}, source_run_id_by_model={"ifs": "synthetic-source"},
        provider_run_id_by_model={"ifs": "synthetic-provider"}, request_hash_by_model={"ifs": "b" * 64})
    topology = [dict(bin_id=str(i), lower_c=low, upper_c=high,
        settlement_step_c=1., rounding_rule=semantics.rounding_rule) for i, (low, high) in enumerate(native_bounds)]
    mixed_q, _, _, _, receipt = _apply_fast_residual_likelihood_to_probability_carrier(
        q={str(i): q for i, q in enumerate(carrier["q"])},
        q_samples_by_bin={str(i): [row[i] for row in carrier["samples"]] for i in range(3)},
        bins=[SimpleNamespace(**row) for row in topology], metric=metric, observed_extreme_c=26.,
        half_step=.5, rounding_rule=semantics.rounding_rule, likelihood=residual)
    likelihood.update(scenario_weights=receipt["scenario_weights"], support_truncation=False)
    conditioning = dict(active=True, support_truncation=False, sample_count=21,
        fast_residual_likelihood=likelihood,
        metric=metric, unit="C", source="wu_api+same_station_fast_tail", observation_time=now.isoformat(),
        observed_extreme_c=26., day0_current_temperature_state=state, day0_remaining_carrier_content_identity=carrier["content_identity"],
        day0_remaining_carrier_operator=carrier["operator"], day0_remaining_carrier_q=carrier["q"],
        day0_remaining_carrier_probability_samples=carrier["samples"], day0_remaining_carrier_sample_count=500,
        day0_remaining_carrier_future_extremes_c=[28., 30.], day0_remaining_carrier_path_error_sigma_c=1.,
        day0_remaining_carrier_probability_cutoff_utc=now.isoformat(), day0_remaining_carrier_likelihood=likelihood,
        day0_remaining_center_policy=DAY0_REMAINING_CENTER_POLICY, day0_probability_mixture_policy=DAY0_PROBABILITY_MIXTURE_POLICY,
        day0_remaining_center_bias_c=0.0,
        day0_remaining_vector_witness=witness, bin_topology=topology)
    event = SimpleNamespace(payload_json=json.dumps(dict(city=city.name, target_date=target, metric=metric, **DAY0_LIVE_AUTHORITY_MATCHES)))
    projected = adapter._global_day0_execution_payload(event,
        family=SimpleNamespace(city=city.name, target_date=target, metric=metric),
        resolution=SimpleNamespace(measurement_unit="C"), conditioning=conditioning,
        observation_conn=conn, decision_time=now, posterior_id=1,
        current_day0_facts=(fact, {**fact, "observed_extreme_native": 26., "observation_source": "aviationweather_metar"}))
    projected.update(metric=metric, target_date=target)
    def replay(payload=None):
        return adapter._day0_remaining_p_raw_vector(np.asarray([28., 30.]), city=city,
            settlement_semantics=semantics, bins=[SimpleNamespace(bin_id=str(i), low=low, high=high) for i, (low, high) in enumerate(native_bounds)],
            payload=projected if payload is None else payload, extra_member_sigma=0., decision_time=now)
    provenance = {**conditioning, "q_shape": "fused_day0_fast_residual_likelihood",
        "day0_provisional_observation": conditioning, "day0_preliminary_report_survival_likelihood": {}}
    return SimpleNamespace(state=state, carrier=carrier, payload=projected, replay=replay,
        expected_q=[mixed_q[str(i)] for i in range(3)], provenance=provenance,
        city=city, target=target, metric=metric, now=now, conn=conn,
        conditioning=conditioning, fact=fact, family=SimpleNamespace(city=city.name,
        target_date=target, metric=metric), event=event)


@pytest.fixture(params=("high", "low"))
def carrier_case(wrh_case, request):
    case = wrh_case
    _write_product(case.conn, case.city, case.request,
        at=case.request.computed_at, values=(26., 24., 25.))
    result = _fast_tail_case(case.conn, case.city, case.request.computed_at,
        metric=request.param)
    result.owner = case
    return result


def _reader_reason(case, provenance=None):
    from src.data.replacement_forecast_bundle_reader import _wu_fast_pinned_carrier_reason
    return _wu_fast_pinned_carrier_reason(
        provenance if provenance is not None else case.provenance,
        city=case.city.name, target_date=case.target, metric=case.metric,
        decision_time=case.now)


def test_native_wrh_revision_survives_persisted_carrier_replay(carrier_case):
    from src.engine import event_reactor_adapter as adapter
    case = carrier_case
    revision = case.state["source_revision_identity"]
    assert len(revision) == 64
    # A durable JSON round trip must retain the writer's exact hash inputs.
    persisted = json.loads(json.dumps(case.payload))
    assert case.replay(persisted).tolist() == pytest.approx(case.expected_q)
    assert adapter._day0_carrier_written_inputs(persisted)["current_path_state"] == case.state


def test_native_wrh_revision_survives_bundle_reader_replay(carrier_case):
    assert _reader_reason(carrier_case) is None



def test_legacy_absent_revision_keeps_old_carrier_identity(carrier_case):
    case = carrier_case
    legacy = _fast_tail_case(case.conn, case.city, case.now,
        metric=case.metric, legacy=True)
    assert "source_revision_identity" not in legacy.state
    assert _reader_reason(legacy) is None
    assert legacy.replay().tolist() == pytest.approx(legacy.expected_q)
    assert legacy.carrier["content_identity"] != case.carrier["content_identity"]
    assert legacy.carrier["q"] == case.carrier["q"]
    from src.engine import event_reactor_adapter as adapter
    adapter._snapshot_day0_source_clock_carrier_provenance(legacy.payload)
    assert "carrier_written_inputs" not in legacy.payload["_edli_day0_source_clock_carrier_provenance"]


@pytest.mark.parametrize("revision", ("bad", "f" * 64, None), ids=("malformed", "forged", "removed"))
def test_altered_frozen_revision_cannot_replay_original_carrier(carrier_case, revision):
    case = carrier_case
    altered = deepcopy(case.provenance)
    if revision is None:
        altered["day0_current_temperature_state"].pop("source_revision_identity")
        case.payload["_edli_day0_carrier_written_inputs"]["current_path_state"].pop("source_revision_identity")
    else:
        altered["day0_current_temperature_state"]["source_revision_identity"] = revision
        case.payload["_edli_day0_carrier_written_inputs"]["current_path_state"]["source_revision_identity"] = revision
    assert _reader_reason(case, altered) in {
        "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_IDENTITY_MISMATCH",
        "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_INVALID",
    }
    with pytest.raises(ValueError, match="DAY0_NOAA_PRELIMINARY_CARRIER_IDENTITY_MISMATCH"):
        case.replay()


def test_later_current_state_cannot_overwrite_frozen_carrier_inputs(carrier_case):
    from src.engine import event_reactor_adapter as adapter
    case = carrier_case
    original = deepcopy(adapter._day0_carrier_written_inputs(case.payload))
    case.payload["_edli_day0_current_temperature_source_revision_identity"] = "b" * 64
    adapter._snapshot_day0_source_clock_carrier_provenance(case.payload)
    assert adapter._day0_carrier_written_inputs(case.payload) == original
    assert case.payload["_edli_day0_source_clock_carrier_provenance"]["carrier_written_inputs"] == original
    assert case.replay().tolist() == pytest.approx(case.expected_q)


def test_current_reader_binds_changed_membership_without_renewing_noop(carrier_case):
    from src.engine import event_reactor_adapter as adapter
    case, owner = carrier_case, carrier_case.owner
    original = deepcopy(adapter._day0_carrier_written_inputs(case.payload))
    identity = {"source_revision_identity": "obsolete"}
    current = adapter._latest_day0_current_temperature_native(world_conn=case.conn,
        family=case.family, decision_time=case.now, identity_out=identity)
    assert identity == case.state
    again, status = _write_product(case.conn, case.city, owner.request,
        at=case.now + timedelta(minutes=1), values=(26., 24., 25.), metadata="transport only")
    assert status == "noop"
    adapter._latest_day0_current_temperature_native(world_conn=case.conn,
        family=case.family, decision_time=case.now + timedelta(minutes=1), identity_out=identity)
    assert identity == case.state
    assert again.received_at == case.now
    _write_product(case.conn, case.city, owner.request,
        at=case.now + timedelta(minutes=2), values=(26., 23., 25.))
    replacement = adapter._latest_day0_current_temperature_native(world_conn=case.conn,
        family=case.family, decision_time=case.now + timedelta(minutes=2), identity_out=identity)
    assert replacement == current
    assert identity["source_revision_identity"] != case.state["source_revision_identity"]
    assert adapter._day0_carrier_written_inputs(case.payload) == original


def test_unrevisioned_current_reader_clears_previous_revision():
    from src.config import runtime_cities_by_name
    from src.engine import event_reactor_adapter as adapter
    from src.state.schema.observation_prints_schema import ensure_table, append_print
    city = runtime_cities_by_name()["Paris"]
    now = datetime(2026, 10, 6, 3, tzinfo=timezone.utc)
    identity = {"source_revision_identity": "f" * 64}
    with sqlite3.connect(":memory:") as conn:
        ensure_table(conn)
        append_print(conn, city=city.name, station_id=city.wu_station,
            source_channel="aviationweather_metar", publish_ts_utc=now.isoformat(),
            value_native=20., unit="C", fetched_at_utc=now.isoformat(),
            raw_report=f"METAR {city.wu_station} {now:%d%H%M}Z 00000KT CAVOK 20/10 Q1010")
        current = adapter._latest_day0_current_temperature_native(world_conn=conn,
            family=SimpleNamespace(city=city.name, target_date="2026-10-06"),
            decision_time=now, identity_out=identity)
    assert current == (20., now, "aviationweather_metar")
    assert identity == {"value_native": 20., "observed_at_utc": now.isoformat(),
        "source": "aviationweather_metar"}


def test_fresh_rebuild_uses_current_revision_and_keeps_original_provenance(carrier_case):
    from src.engine import event_reactor_adapter as adapter
    from src.data.day0_observation_reader import same_station_preliminary_report_survival_likelihood
    case = carrier_case
    likelihood = same_station_preliminary_report_survival_likelihood(case.conn,
        city=case.city.name, station_id=case.city.wu_station,
        timezone_name=case.city.timezone, target_date=case.target,
        temperature_metric=case.metric, decision_time=case.now, allow_prior_only=True)
    assert likelihood is not None
    payload = {
        "metric": case.metric, "target_date": case.target,
        "settlement_source": "aviationweather_metar",
        "evidence_finality": "MONOTONE_SETTLEMENT_BOUND", "rounded_value": 26.,
        "_edli_day0_probability_boundary_native": 26.,
        "_edli_day0_source_clock_predictive_sigma_native": 1.2,
        "_edli_day0_provisional_boundary_survival_probability": likelihood["boundary_survival_probability"],
        "_edli_day0_provisional_revision_likelihood": likelihood,
        "_edli_day0_current_temperature_native": case.state["value_native"],
        "_edli_day0_current_temperature_observed_at_utc": case.state["observed_at_utc"],
        "_edli_day0_current_temperature_source": case.state["source"],
        "_edli_day0_current_temperature_source_revision_identity": case.state["source_revision_identity"],
        "_edli_day0_carrier_written_inputs": deepcopy(case.payload["_edli_day0_carrier_written_inputs"]),
    }
    family = SimpleNamespace(city=case.city.name, target_date=case.target, metric=case.metric,
        candidates=[SimpleNamespace(bin=SimpleNamespace(low=lo, high=hi))
            for lo, hi in ((None, 28.), (29., 29.), (30., None))])
    adapter._rebuild_decision_time_day0_carrier(payload=payload, family=family,
        unit="C", decision_time=case.now, future_extremes_c=(28., 30.),
        authority_kind="held_shared_current_remaining_path", entry_authority=False,
        held_shared_current_remaining_path=True)
    assert payload["_edli_day0_carrier_written_inputs"]["current_path_state"] == case.state
    original = deepcopy(payload["_edli_day0_source_clock_carrier_provenance"])
    original_content = payload["_edli_day0_remaining_content_identity"]
    _write_product(case.conn, case.city, case.owner.request,
        at=case.now + timedelta(minutes=1), values=(26., 23., 25.))
    current_identity = {}
    adapter._latest_day0_current_temperature_native(world_conn=case.conn,
        family=family, decision_time=case.now + timedelta(minutes=1), identity_out=current_identity)
    payload["_edli_day0_current_temperature_source_revision_identity"] = current_identity["source_revision_identity"]
    # Rebuild at the new receipt with unchanged spot, boundary and future values.
    adapter._rebuild_decision_time_day0_carrier(payload=payload, family=family,
        unit="C", decision_time=case.now + timedelta(minutes=1), future_extremes_c=(28., 30.),
        authority_kind="held_shared_current_remaining_path", entry_authority=False,
        held_shared_current_remaining_path=True)
    assert payload["_edli_day0_carrier_written_inputs"]["current_path_state"] == current_identity
    assert payload["_edli_day0_remaining_content_identity"] != original_content
    assert payload["_edli_day0_source_clock_carrier_provenance"] == original
    assert original["carrier_written_inputs"]["current_path_state"] == case.state



def test_malformed_present_revision_cannot_enter_written_inputs(carrier_case):
    from src.engine import event_reactor_adapter as adapter
    payload = dict(carrier_case.payload)
    payload.pop("_edli_day0_carrier_written_inputs")
    payload["_edli_day0_current_temperature_source_revision_identity"] = "bad"
    with pytest.raises(ValueError, match="CURRENT_TEMPERATURE_SOURCE_REVISION_INVALID"):
        adapter._bind_day0_carrier_written_inputs(payload)


def test_unqualified_product_cannot_lend_its_revision_to_current_fast_state(carrier_case):
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.engine import event_reactor_adapter as adapter
    case = carrier_case
    # Corrupt only this test database's claimed current product proof. The
    # separate current AWC point remains available with no WRH dependency.
    case.conn.execute("UPDATE observations SET high_provenance_metadata=? WHERE city=? AND target_date=?",
        ("{}", case.city.name, case.target))
    case.conn.commit()
    owned, snapshot = read_current_noaa_wrh_snapshot(case.conn, city=case.city,
        target_date=case.target, as_of=case.now)
    assert owned and snapshot is None
    current_identity = dict(case.state)
    current = adapter._latest_day0_current_temperature_native(world_conn=case.conn,
        family=case.family, decision_time=case.now, identity_out=current_identity)
    assert current[2] == "aviationweather_metar"
    assert "source_revision_identity" not in current_identity
    assert adapter._day0_carrier_written_inputs(case.payload)["current_path_state"] == case.state
