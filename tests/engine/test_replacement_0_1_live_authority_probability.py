# Created: 2026-06-07
# Last reused/audited: 2026-07-27
# Authority basis: Operator 2026-06-07 live cutover directive: replacement 0.1
#   posterior is the live forecast authority; NO probabilities must not be
#   inferred from YES complements.

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.calibration.qlcb_provenance import _qlcb_float
from src.contracts.execution_price import ExecutionPrice
from src.engine import event_reactor_adapter as adapter
from src.events.candidate_binding import weather_family_id
from src.events.opportunity_event import make_opportunity_event
from src.solve.solver import (
    JointOutcomeProbabilityWitness,
    OutcomeTokenBinding,
    joint_probability_witness_identity,
)
from src.types.market import Bin


def _family() -> SimpleNamespace:
    return SimpleNamespace(
        city="Testopolis",
        target_date="2026-06-09",
        metric="high",
        candidates=(
            SimpleNamespace(
                condition_id="cond-27",
                yes_token_id="yes-27",
                no_token_id="no-27",
                bin=Bin(low=27.0, high=27.0, unit="C", label="27°C"),
            ),
            SimpleNamespace(
                condition_id="cond-28",
                yes_token_id="yes-28",
                no_token_id="no-28",
                bin=Bin(low=28.0, high=28.0, unit="C", label="28°C"),
            ),
        ),
    )


def _replacement_bundle() -> SimpleNamespace:
    return SimpleNamespace(
        posterior_id=123,
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        source_cycle_time="2026-06-07T00:00:00+00:00",
        computed_at="2026-06-07T00:05:00+00:00",
        q={
            "bin-27": 0.20,
            "bin-28": 0.80,
        },
        q_lcb={
            "bin-27": 0.10,
            "bin-28": 0.70,
        },
        q_ucb={
            "bin-27": 1.0,
            "bin-28": 1.0,
        },
        provenance_json={
            # FIX 1 (2026-06-09): the live q-mode gate runs before this proof's logic and admits
            # only the fused-Normal modes. This fixture exercises the downstream YES-posterior /
            # native-NO direction relationship, so it carries a live-eligible mode to reach it.
            "replacement_q_mode": "FUSED_NORMAL_FULL",
            "q_shape": "fused_normal_direct",
            "q_lcb_basis": "fused_center_bootstrap_p05",
            "q_bootstrap_samples_by_bin": {
                "bin-27": [0.20] * 200,
                "bin-28": [0.80] * 200,
            },
            "bin_topology": [
                {"bin_id": "bin-27", "lower_c": 27.0, "upper_c": 27.0},
                {"bin_id": "bin-28", "lower_c": 28.0, "upper_c": 28.0},
            ],
        },
    )


def _fast_residual_conditioning(
    *,
    observed_extreme_c: float = 28.0,
    observation_time: str = "2026-06-09T10:00:00+00:00",
    source: str = "aviationweather_metar",
) -> dict[str, object]:
    residual_weights = ((0.0, 0.9),)
    identity = {
        "semantics_revision": "same_station_causal_residual_v1",
        "station_id": "TEST",
        "settlement_channel": "wu_icao_history",
        "fast_channel": "aviationweather_metar",
        "unit": "C",
        "as_of": observation_time,
        "window_start": "2026-06-02T10:00:00+00:00",
        "matched_pairs": 20,
        "residual_weights_c": residual_weights,
        "unknown_weight": 0.1,
        "settlement_extreme_c": 27.0,
    }
    identity_hash = hashlib.sha256(
        json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "active": True,
        "metric": "high",
        "observed_extreme_c": observed_extreme_c,
        "source": source,
        "observation_time": observation_time,
        "sample_count": 21,
        "unit": "C",
        "support_truncation": False,
        "fast_residual_likelihood": {
            **identity,
            "identity_hash": identity_hash,
            "residual_weights_c": [
                {"residual_c": residual, "weight": weight}
                for residual, weight in residual_weights
            ],
            "scenario_weights": [
                {"observed_bound_c": observed_extreme_c, "weight": 0.9},
                {"observed_bound_c": 27.0, "weight": 0.1},
            ],
            "support_truncation": False,
        },
    }


@pytest.mark.parametrize(
    "conditioning_source",
    ("aviationweather_metar", "wu_api+same_station_fast_tail"),
)
def test_fast_residual_bundle_is_one_conditioned_day0_probability_world(
    conditioning_source: str,
) -> None:
    family = _family()
    bindings = tuple(
        OutcomeTokenBinding(
            bin_id=f"bin-{int(candidate.bin.low)}",
            condition_id=candidate.condition_id,
            yes_token_id=candidate.yes_token_id,
            no_token_id=candidate.no_token_id,
        )
        for candidate in family.candidates
    )
    bundle = _replacement_bundle()
    bundle.provenance_json.update(
        {
            "q_bootstrap_samples_basis": (
                "day0_fast_residual_joint_simplex_v1"
            ),
            "day0_provisional_observation": _fast_residual_conditioning(
                source=conditioning_source,
            ),
        }
    )

    components = adapter._replacement_global_probability_components(
        bundle,
        candidates=family.candidates,
        bindings=bindings,
    )

    assert components is not None
    samples, point_q, basis = components
    assert samples[0].tolist() == pytest.approx([0.2, 0.8])
    assert point_q.tolist() == pytest.approx([0.2, 0.8])
    assert basis == (
        adapter._GLOBAL_DAY0_CONDITIONED_REPLACEMENT_SIMPLEX_BAND_BASIS
    )
    forged = json.loads(
        json.dumps(bundle.provenance_json["day0_provisional_observation"])
    )
    forged["fast_residual_likelihood"]["identity_hash"] = "0" * 64
    bundle.provenance_json["day0_provisional_observation"] = forged
    with pytest.raises(
        ValueError,
        match="GLOBAL_DAY0_FAST_RESIDUAL_POSTERIOR_IDENTITY_INVALID",
    ):
        adapter._replacement_global_probability_components(
            bundle,
            candidates=family.candidates,
            bindings=bindings,
        )


def test_fast_residual_simplex_canonicalizes_only_ieee754_boundary_dust() -> None:
    family = _family()
    bindings = tuple(
        OutcomeTokenBinding(
            bin_id=f"bin-{int(candidate.bin.low)}",
            condition_id=candidate.condition_id,
            yes_token_id=candidate.yes_token_id,
            no_token_id=candidate.no_token_id,
        )
        for candidate in family.candidates
    )
    bundle = _replacement_bundle()
    bundle.q = {"bin-27": 1.0, "bin-28": 0.0}
    bundle.provenance_json.update(
        {
            "q_bootstrap_samples_basis": "day0_fast_residual_joint_simplex_v1",
            "day0_provisional_observation": _fast_residual_conditioning(),
            "q_bootstrap_samples_by_bin": {
                "bin-27": [np.nextafter(1.0, 2.0), 0.2],
                "bin-28": [0.0, 0.8],
            },
        }
    )

    components = adapter._replacement_global_probability_components(
        bundle,
        candidates=family.candidates,
        bindings=bindings,
    )

    assert components is not None
    samples, point_q, _basis = components
    assert samples[0].tolist() == [1.0, 0.0]
    assert np.allclose(samples.sum(axis=1), 1.0, rtol=0.0, atol=1e-15)
    assert point_q.tolist() == [1.0, 0.0]

    bundle.provenance_json["q_bootstrap_samples_by_bin"] = {
        "bin-27": [1.0 + 2e-9, 0.2],
        "bin-28": [-2e-9, 0.8],
    }
    assert (
        adapter._replacement_global_probability_components(
            bundle,
            candidates=family.candidates,
            bindings=bindings,
        )
        is None
    )


def test_day0_execution_payload_binds_fast_probability_without_promoting_settlement_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.data import day0_fast_obs
    from src.data import replacement_forecast_current_target_plan as target_plan

    settlement_fact = {
        "observation_source": "wu_icao_history",
        "observation_time": "2026-06-09T09:00:00+00:00",
        "observation_available_at": "2026-06-09T09:01:00+00:00",
        "observed_extreme_native": 27.0,
        "sample_count": 10,
        "station_id": "TEST",
        "unit": "C",
    }
    monkeypatch.setattr(
        target_plan,
        "_latest_authorized_day0_fact",
        lambda *_args, **kwargs: (
            settlement_fact if kwargs["require_settlement_channel"] else None
        ),
    )
    monkeypatch.setattr(
        day0_fast_obs,
        "latest_fast_station_extreme_c",
        lambda *_args, **_kwargs: (
            28.0,
            "2026-06-09T10:00:00+00:00",
            21,
            "C",
        ),
    )
    city = SimpleNamespace(
        name="Testopolis",
        timezone="UTC",
        settlement_unit="C",
        settlement_source_type="wu_icao",
        wu_station="TEST",
    )
    monkeypatch.setattr(
        adapter,
        "runtime_cities_by_name",
        lambda: {"Testopolis": city},
    )
    monkeypatch.setattr(
        adapter.SettlementSemantics,
        "for_city",
        lambda _city: SimpleNamespace(round_single=lambda value: int(value)),
    )
    event = SimpleNamespace(
        payload_json=json.dumps(
            {
                "city": "Testopolis",
                "target_date": "2026-06-09",
                "metric": "high",
                "source_match_status": "MATCH",
                "local_date_status": "MATCH",
                "station_match_status": "MATCH",
                "dst_status": "UNAMBIGUOUS",
                "metric_match_status": "MATCH",
                "rounding_status": "MATCH",
                "source_authorized_status": "AUTHORIZED",
                "live_authority_status": "live",
            }
        )
    )

    payload = adapter._global_day0_execution_payload(
        event,
        family=SimpleNamespace(
            city="Testopolis",
            target_date="2026-06-09",
            metric="high",
        ),
        resolution=SimpleNamespace(measurement_unit="C"),
        conditioning=_fast_residual_conditioning(),
        observation_conn=sqlite3.connect(":memory:"),
        decision_time=datetime(2026, 6, 9, 10, 5, tzinfo=timezone.utc),
        posterior_id=123,
        probability_base_identity="posterior-fast",
    )

    assert payload["settlement_source"] == "wu_icao_history"
    assert payload["raw_value"] == 27.0
    statistical = payload["_edli_global_day0_binding"][
        "statistical_probability_conditioning"
    ]
    assert statistical["source"] == "aviationweather_metar"
    assert statistical["observed_extreme_c"] == 28.0


def test_current_global_probability_authority_rebuilds_canonical_matrix_and_refutes_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.data import replacement_forecast_bundle_reader as reader
    from src.data import replacement_forecast_readiness as readiness_reader

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE forecast_posteriors "
        "(posterior_id INTEGER PRIMARY KEY, posterior_identity_hash TEXT)"
    )
    conn.execute(
        "CREATE TABLE market_events ("
        "city TEXT, target_date TEXT, temperature_metric TEXT, condition_id TEXT, "
        "market_slug TEXT, range_label TEXT, range_low REAL, range_high REAL, "
        "outcome TEXT, token_id TEXT)"
    )
    conn.executemany(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                "Testopolis",
                "2026-06-09",
                "high",
                "cond-27",
                "market-27",
                "27C",
                27.0,
                27.0,
                "bin-27",
                "yes-27",
            ),
            (
                "Testopolis",
                "2026-06-09",
                "high",
                "cond-28",
                "market-28",
                "28C",
                28.0,
                28.0,
                "bin-28",
                "yes-28",
            ),
        ),
    )
    posterior_identity = "canonical-posterior-current"
    conn.execute(
        "INSERT INTO forecast_posteriors VALUES (?, ?)",
        (123, posterior_identity),
    )
    bundle = _replacement_bundle()
    bundle.provenance_json["q_bootstrap_samples_basis"] = "global_simplex_v1"
    bundle.provenance_json["q_bootstrap_samples_by_bin"] = {
        "bin-27": [0.20] * 400,
        "bin-28": [0.80] * 400,
    }
    monkeypatch.setattr(
        readiness_reader,
        "latest_replacement_readiness",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr(
        reader,
        "read_replacement_forecast_bundle",
        lambda *a, **k: SimpleNamespace(
            ok=True,
            bundle=bundle,
            reason_code="READY",
        ),
    )
    event = SimpleNamespace(
        payload_json=json.dumps(
            {
                "city": "Testopolis",
                "target_date": "2026-06-09",
                "metric": "high",
                "unit": "C",
            }
        )
    )
    bindings = (
        OutcomeTokenBinding("internal-27", "cond-27", "yes-27", "no-27"),
        OutcomeTokenBinding("internal-28", "cond-28", "yes-28", "no-28"),
    )
    samples = np.column_stack(
        (np.full(400, 0.20), np.full(400, 0.80))
    )
    decision_time = datetime(2026, 6, 7, tzinfo=timezone.utc)
    family_key = weather_family_id(
        city="Testopolis",
        target_date="2026-06-09",
        metric="high",
    )
    witness_identity = joint_probability_witness_identity(
        family_key=family_key,
        bindings=bindings,
        q_version="q-current",
        resolution_identity="resolution-current",
        topology_identity="topology-current",
        posterior_identity_hash=posterior_identity,
        source_truth_identity="source-current",
        authority_certificate_hash="certificate-current",
        band_alpha=0.05,
        band_basis="PARAMETER_POSTERIOR_SIMPLEX_V1",
        yes_point_q=np.mean(samples, axis=0),
        yes_q_samples=samples,
        captured_at_utc=decision_time,
    )
    witness = JointOutcomeProbabilityWitness(
        family_key=family_key,
        bindings=bindings,
        yes_point_q=np.mean(samples, axis=0),
        yes_q_samples=samples,
        q_version="q-current",
        resolution_identity="resolution-current",
        topology_identity="topology-current",
        posterior_identity_hash=posterior_identity,
        source_truth_identity="source-current",
        authority_certificate_hash="certificate-current",
        band_alpha=0.05,
        band_basis="PARAMETER_POSTERIOR_SIMPLEX_V1",
        captured_at_utc=decision_time,
        max_age=timedelta(seconds=30),
        witness_identity=witness_identity,
    )

    current = adapter.current_global_probability_authority(
        conn,
        event,
        witness,
        decision_time=decision_time,
    )
    assert current is not None
    assert current.posterior_identity_hash == posterior_identity

    changed_point_q = np.asarray((0.21, 0.79), dtype=np.float64)
    changed_point_identity = joint_probability_witness_identity(
        family_key=witness.family_key,
        bindings=witness.bindings,
        q_version=witness.q_version,
        resolution_identity=witness.resolution_identity,
        topology_identity=witness.topology_identity,
        posterior_identity_hash=witness.posterior_identity_hash,
        source_truth_identity=witness.source_truth_identity,
        authority_certificate_hash=witness.authority_certificate_hash,
        band_alpha=witness.band_alpha,
        band_basis=witness.band_basis,
        yes_point_q=changed_point_q,
        yes_q_samples=witness.yes_q_samples,
        captured_at_utc=witness.captured_at_utc,
    )
    changed_point_witness = replace(
        witness,
        yes_point_q=changed_point_q,
        witness_identity=changed_point_identity,
    )
    assert adapter.current_global_probability_authority(
        conn,
        event,
        changed_point_witness,
        decision_time=decision_time,
    ) is None

    # A provisional Day0 replacement witness has the ordinary current
    # settlement-simplex basis. It must re-read the canonical posterior here,
    # not take the hard-fact Day0 age-only shortcut.
    day0_basis = adapter._GLOBAL_CURRENT_SETTLEMENT_SIMPLEX_BAND_BASIS
    day0_identity = joint_probability_witness_identity(
        family_key=family_key,
        bindings=bindings,
        q_version="q-current",
        resolution_identity="resolution-current",
        topology_identity="topology-current",
        posterior_identity_hash=posterior_identity,
        source_truth_identity="source-current",
        authority_certificate_hash="certificate-current",
        band_alpha=0.05,
        band_basis=day0_basis,
        yes_point_q=np.mean(samples, axis=0),
        yes_q_samples=samples,
        captured_at_utc=decision_time,
    )
    day0_witness = JointOutcomeProbabilityWitness(
        family_key=family_key,
        bindings=bindings,
        yes_point_q=np.mean(samples, axis=0),
        yes_q_samples=samples,
        q_version="q-current",
        resolution_identity="resolution-current",
        topology_identity="topology-current",
        posterior_identity_hash=posterior_identity,
        source_truth_identity="source-current",
        authority_certificate_hash="certificate-current",
        band_alpha=0.05,
        band_basis=day0_basis,
        captured_at_utc=decision_time,
        max_age=timedelta(seconds=30),
        witness_identity=day0_identity,
    )
    day0_event = SimpleNamespace(
        event_type="DAY0_EXTREME_UPDATED",
        payload_json=event.payload_json,
    )
    assert adapter.current_global_probability_authority(
        conn,
        day0_event,
        day0_witness,
        decision_time=decision_time,
    ) is not None

    # A mature Day0 conditioned witness binds the raw replacement posterior to
    # the current observation, so its posterior identity is intentionally
    # composite. The winner preflight has already rebuilt and content-matched
    # that witness; this final certificate seam must validate its age and shape
    # instead of comparing the composite identity to the raw posterior row.
    conditioned_basis = (
        adapter._GLOBAL_DAY0_CONDITIONED_REPLACEMENT_SIMPLEX_BAND_BASIS
    )
    conditioned_identity = joint_probability_witness_identity(
        family_key=family_key,
        bindings=bindings,
        q_version="q-conditioned",
        resolution_identity="resolution-current",
        topology_identity="topology-current",
        posterior_identity_hash="conditioned-posterior-identity",
        source_truth_identity="conditioned-source-truth",
        authority_certificate_hash="conditioned-certificate",
        band_alpha=0.05,
        band_basis=conditioned_basis,
        yes_point_q=np.mean(samples, axis=0),
        yes_q_samples=samples,
        captured_at_utc=decision_time,
    )
    conditioned_witness = JointOutcomeProbabilityWitness(
        family_key=family_key,
        bindings=bindings,
        yes_point_q=np.mean(samples, axis=0),
        yes_q_samples=samples,
        q_version="q-conditioned",
        resolution_identity="resolution-current",
        topology_identity="topology-current",
        posterior_identity_hash="conditioned-posterior-identity",
        source_truth_identity="conditioned-source-truth",
        authority_certificate_hash="conditioned-certificate",
        band_alpha=0.05,
        band_basis=conditioned_basis,
        captured_at_utc=decision_time,
        max_age=timedelta(seconds=30),
        witness_identity=conditioned_identity,
    )
    assert adapter.current_global_probability_authority(
        conn,
        day0_event,
        conditioned_witness,
        decision_time=decision_time,
    ) is not None
    assert adapter.current_global_probability_authority(
        conn,
        day0_event,
        conditioned_witness,
        decision_time=decision_time + timedelta(seconds=31),
    ) is None

    conn.execute(
        "UPDATE forecast_posteriors SET posterior_identity_hash = ? WHERE posterior_id = ?",
        ("posterior-superseded", 123),
    )
    assert adapter.current_global_probability_authority(
        conn,
        event,
        witness,
        decision_time=decision_time,
    ) is None
    assert adapter.current_global_probability_authority(
        conn,
        day0_event,
        day0_witness,
        decision_time=decision_time,
    ) is None
    conn.close()


@pytest.mark.parametrize(
    "reason",
    (
        "GLOBAL_CURRENT_POSTERIOR_IDENTITY_INCOMPLETE",
        "GLOBAL_CURRENT_POSTERIOR_SIMPLEX_INVALID",
        "GLOBAL_DAY0_SOURCE_AVAILABLE_AT_INVALID",
        "GLOBAL_DAY0_SOURCE_CYCLE_INVALID",
        "GLOBAL_DAY0_PHYSICAL_FRONTIER_NOT_SETTLEMENT_CONFIRMED",
        "GLOBAL_DAY0_PROVISIONAL_OBSERVATION_NOT_ENTRY_AUTHORITY",
    ),
)
def test_current_probability_failure_is_family_local(reason: str) -> None:
    assert adapter._is_global_probability_family_unavailable(
        ValueError(reason)
    ) is True


@pytest.mark.parametrize(
    ("metric", "physical", "settlement", "expected"),
    (
        ("high", 86.0, 84.0, True),
        ("high", 84.0, 84.0, False),
        ("low", 23.0, 25.0, True),
        ("low", 25.0, 25.0, False),
    ),
)
def test_day0_physical_frontier_invalidates_stale_entry_belief(
    metric: str,
    physical: float,
    settlement: float,
    expected: bool,
) -> None:
    assert adapter._day0_physical_frontier_supersedes_settlement(
        metric=metric,
        physical_fact={"observed_extreme_native": physical},
        settlement_fact={"observed_extreme_native": settlement},
    ) is expected


def test_day0_physical_frontier_without_settlement_fact_blocks_entry_belief() -> None:
    assert adapter._day0_physical_frontier_supersedes_settlement(
        metric="high",
        physical_fact={"observed_extreme_native": 86.0},
        settlement_fact=None,
    ) is True


def test_global_provisional_day0_uses_physical_source_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.data import replacement_forecast_bundle_reader as reader
    from src.data import replacement_forecast_current_target_plan as target_plan
    from src.data import replacement_forecast_readiness as readiness_reader
    from src.execution import day0_hard_fact_exit

    forecast = sqlite3.connect(":memory:")
    forecast.row_factory = sqlite3.Row
    forecast.execute(
        "CREATE TABLE market_events ("
        "city TEXT, target_date TEXT, temperature_metric TEXT, "
        "condition_id TEXT, token_id TEXT, market_slug TEXT, "
        "range_label TEXT, range_low REAL, range_high REAL)"
    )
    forecast.executemany(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                "Hong Kong",
                "2026-06-09",
                "high",
                "cond-27",
                "yes-27",
                "test-27",
                "27C or below",
                None,
                27.0,
            ),
            (
                "Hong Kong",
                "2026-06-09",
                "high",
                "cond-28",
                "yes-28",
                "test-28",
                "28C or above",
                28.0,
                None,
            ),
        ),
    )
    observations = sqlite3.connect(":memory:")
    observations.execute(
        "CREATE TABLE observation_instants ("
        "city TEXT, target_date TEXT, running_max REAL, utc_timestamp TEXT, "
        "local_timestamp TEXT, source TEXT, causality_status TEXT, "
        "authority TEXT, source_role TEXT, training_allowed INTEGER)"
    )
    observations.execute(
        "INSERT INTO observation_instants VALUES "
        "('Hong Kong','2026-06-09',27.0,'2026-06-09T10:00:00+00:00',"
        "'2026-06-09T10:00:00+00:00','ogimet_metar_test','CAUSAL',"
        "'VERIFIED','settlement_channel',0)"
    )

    settlement_fact = {
        "observation_source": "ogimet_metar_test",
        "observation_time": "2026-06-09T10:01:00+00:00",
        "observed_extreme_native": 28.0,
    }
    physical_fact = {
        "observation_source": "aviationweather_metar",
        "observation_time": "2026-06-09T10:00:00+00:00",
        "observed_extreme_native": 27.0,
    }
    returned_b = {
        "settlement_source": settlement_fact["observation_source"],
        "observation_time": settlement_fact["observation_time"],
        "observed_extreme_native": settlement_fact["observed_extreme_native"],
        "high_so_far": settlement_fact["observed_extreme_native"],
        "settlement_unit": "C",
    }
    bundle = SimpleNamespace(
        posterior_id=123,
        posterior_identity_hash="posterior-a",
        dependency_hash="dependency-a",
        posterior_config_hash="config-a",
        source_cycle_time="2026-06-09T00:00:00+00:00",
        source_available_at="2026-06-09T06:00:00+00:00",
        provenance_json={
            "day0_provisional_observation": {
                "active": True,
                "support_truncation": False,
                "source": physical_fact["observation_source"],
                "observation_time": physical_fact["observation_time"],
                "observed_extreme_c": physical_fact["observed_extreme_native"],
            },
            "bayes_precision_fusion": {"predictive_sigma_c": 2.0},
        },
    )
    monkeypatch.setattr(
        adapter,
        "runtime_cities_by_name",
        lambda: {
            "Hong Kong": SimpleNamespace(
                timezone="UTC",
                settlement_unit="C",
                settlement_source_type="wu_icao",
                wu_station="VHHH",
            )
        },
    )
    monkeypatch.setattr(
        day0_hard_fact_exit,
        "_final_daily_observation_extreme",
        lambda **_kwargs: None,
    )
    fact_requests: list[bool] = []

    def latest_fact(*_args, **kwargs):
        settlement_channel = bool(kwargs["require_settlement_channel"])
        fact_requests.append(settlement_channel)
        return settlement_fact if settlement_channel else physical_fact

    monkeypatch.setattr(target_plan, "_latest_authorized_day0_fact", latest_fact)
    monkeypatch.setattr(
        readiness_reader,
        "latest_replacement_readiness",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        reader,
        "read_replacement_forecast_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(
            ok=True,
            bundle=bundle,
            reason_code="READY",
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_global_day0_execution_payload",
        lambda *_args, **_kwargs: returned_b,
    )
    event = make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key="Hong Kong|2026-06-09|high",
        source="test",
        observed_at=settlement_fact["observation_time"],
        available_at=settlement_fact["observation_time"],
        received_at=settlement_fact["observation_time"],
        payload={
            "city": "Hong Kong",
            "target_date": "2026-06-09",
            "metric": "high",
            "unit": "C",
            "settlement_source": settlement_fact["observation_source"],
            "settlement_unit": "C",
            "observation_time": settlement_fact["observation_time"],
            "raw_value": settlement_fact["observed_extreme_native"],
            "rounded_value": 28,
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
        },
        causal_snapshot_id="day0-a",
    )

    prepare_kwargs = {
        "forecast_conn": forecast,
        "topology_conn": forecast,
        "observation_conn": observations,
        "decision_time": datetime(2026, 6, 9, 12, tzinfo=timezone.utc),
        "max_age": timedelta(seconds=30),
        "allow_provisional_day0_replacement": True,
        "probability_use": adapter._CurrentProbabilityUse.HELD_MONITOR,
    }
    adapter._prepare_current_global_probability_family(event, **prepare_kwargs)
    assert fact_requests[:2] == [True, False]

    for key, bad_value in (
        ("observation_source", "aviationweather_other"),
        ("observation_time", "2026-06-09T10:02:00+00:00"),
        ("observed_extreme_native", 26.0),
    ):
        original = physical_fact[key]
        physical_fact[key] = bad_value
        with pytest.raises(
            ValueError,
            match="GLOBAL_DAY0_PROVISIONAL_POSTERIOR_IDENTITY_MISMATCH",
        ):
            adapter._prepare_current_global_probability_family(
                event,
                **prepare_kwargs,
            )
        physical_fact[key] = original
    forecast.close()
    observations.close()


def test_provisional_identity_uses_statistical_conditioning_not_settlement_bound() -> None:
    provisional = {
        "active": True,
        "source": "wu_api+same_station_fast_tail",
        "observation_time": "2026-08-22T02:00:56+00:00",
        "observed_extreme_c": 31.0,
        "support_truncation": False,
    }
    bundle = SimpleNamespace(
        provenance_json={"day0_provisional_observation": provisional}
    )
    payload = {
        "metric": "high",
        "settlement_source": "wu_icao_history",
        "observation_time": "2026-08-22T01:00:00+00:00",
        "high_so_far": 29.0,
        "settlement_unit": "C",
        "_edli_global_day0_binding": {
            "statistical_probability_conditioning": {
                **provisional,
                "metric": "high",
                "unit": "C",
            }
        },
    }

    adapter._assert_provisional_day0_replacement_bundle(bundle, payload)

    payload["_edli_global_day0_binding"]["statistical_probability_conditioning"] = {
        **provisional,
        "metric": "high",
        "unit": "C",
        "observed_extreme_c": 30.0,
    }
    with pytest.raises(
        ValueError,
        match="GLOBAL_DAY0_PROVISIONAL_POSTERIOR_IDENTITY_MISMATCH",
    ):
        adapter._assert_provisional_day0_replacement_bundle(bundle, payload)


def test_provisional_local_proof_preserves_global_statistical_conditioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conditioning = {
        "active": True,
        "source": "wu_api+same_station_fast_tail",
        "observation_time": "2026-08-22T02:24:13+00:00",
        "observed_extreme_c": 14.0,
        "support_truncation": False,
        "metric": "low",
        "unit": "C",
    }
    bundle = SimpleNamespace(
        posterior_id=374802,
        provenance_json={"day0_provisional_observation": conditioning},
    )
    payload = {
        "city": "London",
        "target_date": "2026-08-22",
        "metric": "low",
        "settlement_source": "wu_icao_history",
        "observation_time": "2026-08-22T02:00:00+00:00",
        "low_so_far": 15.0,
        "settlement_unit": "C",
        "_edli_global_day0_binding": {
            "statistical_probability_conditioning": conditioning,
        },
    }
    event = SimpleNamespace(event_type="DAY0_EXTREME_UPDATED")
    family = SimpleNamespace(city="London", target_date="2026-08-22", metric="low")
    expected = ({"condition": 0.8}, {}, {}, {}, {})
    observed_conditionings = []

    monkeypatch.setattr(
        adapter,
        "runtime_cities_by_name",
        lambda: {"London": SimpleNamespace(settlement_unit="C")},
    )

    def replacement(**kwargs):
        assert kwargs["payload"]["_edli_global_day0_binding"][
            "statistical_probability_conditioning"
        ] == conditioning
        kwargs["payload"]["_edli_spine_posterior_id"] = bundle.posterior_id
        kwargs["payload"]["_edli_spine_posterior_identity_hash"] = "posterior-current"
        kwargs["provenance_capture"]["replacement_bundle"] = bundle
        return expected

    monkeypatch.setattr(
        adapter,
        "_replacement_authority_probability_and_fdr_proof",
        replacement,
    )

    def current_observation(*_args, **kwargs):
        observed_conditionings.append(kwargs["conditioning"])
        return {
            **payload,
            "_edli_global_day0_binding": {
                "statistical_probability_conditioning": kwargs["conditioning"],
                "posterior_id": bundle.posterior_id,
            },
        }

    monkeypatch.setattr(
        adapter,
        "_global_day0_execution_payload",
        current_observation,
    )

    assert adapter._live_yes_probabilities(
        event=event,
        payload=payload,
        family=family,
        conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        native_costs={},
        decision_time=datetime(2026, 8, 22, 2, 30, tzinfo=timezone.utc),
        provenance_capture={},
    ) == expected
    assert observed_conditionings == [conditioning]


def test_replacement_yes_lcb_ignores_aifs_provenance_fallback() -> None:
    bundle = SimpleNamespace(
        q_lcb=None,
        provenance_json={
            "aifs_member_count": 51,
            "aifs_probabilities": {"bin-28": 1.0},
        },
    )

    assert adapter._replacement_yes_lcb_for_bin(
        bundle,
        bin_id="bin-28",
        q_yes=0.80,
        settlement_floor_lcb=None,
    ) == 0.0


def test_replacement_intermediate_cycles_keep_live_horizon_profile() -> None:
    assert adapter._posterior_horizon_profile("2026-06-18T00:00:00+00:00") == "full"
    assert adapter._posterior_horizon_profile("2026-06-18T06:00:00+00:00") == "full"
    assert adapter._posterior_horizon_profile("2026-06-18T12:00:00+00:00") == "full"
    assert adapter._posterior_horizon_profile("2026-06-18T18:00:00+00:00") == "full"
