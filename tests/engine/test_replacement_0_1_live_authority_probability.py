# Created: 2026-06-07
# Last reused/audited: 2026-07-19
# Authority basis: Operator 2026-06-07 live cutover directive: replacement 0.1
#   posterior is the live forecast authority; NO probabilities must not be
#   inferred from YES complements.

from __future__ import annotations

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


def _passing_evidence():
    from src.data.replacement_forecast_runtime_policy import ReplacementForecastPromotionEvidence

    return ReplacementForecastPromotionEvidence(
        official_days=5,
        official_rows=250,
        after_cost_pnl=1.0,
        q_lcb_coverage=0.95,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=250,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        nested_holdout_brier=0.20,
        nested_holdout_log_loss=0.50,
        nested_selected_anchor_weight=0.80,
        nested_selected_anchor_sigma_c=3.00,
        nested_guardrail_bucket_count=1,
        nested_guardrail_bucket_min_rows=20,
        product_specific_refit_passed=True,
    )


def _capital_objective_evidence():
    from src.data.replacement_forecast_runtime_policy import (
        EXPECTED_CAPITAL_OBJECTIVE_LABEL,
        ReplacementForecastCapitalObjectiveEvidence,
    )

    return ReplacementForecastCapitalObjectiveEvidence(
        selected_label=EXPECTED_CAPITAL_OBJECTIVE_LABEL,
        replay_status="EMPIRICAL_WINNER",
        after_cost_pnl=1.0,
        source_availability_observed=True,
        source_availability_violations=0,
        anti_lookahead_violations=0,
        same_clob_replay_passed=True,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        product_specific_refit_passed=True,
    )




def test_current_global_probability_authority_rebuilds_canonical_matrix_and_refutes_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import replacement_forecast_hook_factory as hook_factory

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
        hook_factory,
        "_latest_replacement_readiness",
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


def test_global_provisional_day0_rejects_observation_advance_after_bundle_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.data import replacement_forecast_bundle_reader as reader
    from src.data import replacement_forecast_current_target_plan as target_plan
    from src.engine import replacement_forecast_hook_factory as hook_factory
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
        "'2026-06-09T10:00:00+00:00','hko_hourly_accumulator','CAUSAL',"
        "'VERIFIED','settlement_channel',0)"
    )

    fact_a = {
        "observation_source": "hko_hourly_accumulator",
        "observation_time": "2026-06-09T10:00:00+00:00",
        "observed_extreme_native": 27.0,
    }
    returned_b = {
        "settlement_source": "hko_hourly_accumulator",
        "observation_time": "2026-06-09T10:01:00+00:00",
        "observed_extreme_native": 28.0,
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
                "source": fact_a["observation_source"],
                "observation_time": fact_a["observation_time"],
                "observed_extreme_c": fact_a["observed_extreme_native"],
            }
        },
    )
    monkeypatch.setattr(
        adapter,
        "runtime_cities_by_name",
        lambda: {
            "Hong Kong": SimpleNamespace(
                timezone="UTC",
                settlement_unit="C",
            )
        },
    )
    monkeypatch.setattr(
        day0_hard_fact_exit,
        "_final_daily_observation_extreme",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        target_plan,
        "_latest_authorized_day0_fact",
        lambda *_args, **_kwargs: fact_a,
    )
    monkeypatch.setattr(
        hook_factory,
        "_latest_replacement_readiness",
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
        observed_at=fact_a["observation_time"],
        available_at=fact_a["observation_time"],
        received_at=fact_a["observation_time"],
        payload={
            "city": "Hong Kong",
            "target_date": "2026-06-09",
            "metric": "high",
            "unit": "C",
            "settlement_source": "hko_hourly_accumulator",
            "settlement_unit": "C",
            "observation_time": fact_a["observation_time"],
            "raw_value": 27.0,
            "rounded_value": 27,
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

    with pytest.raises(
        ValueError,
        match="GLOBAL_DAY0_PROVISIONAL_OBSERVATION_NOT_ENTRY_AUTHORITY",
    ):
        adapter._prepare_current_global_probability_family(
            event,
            forecast_conn=forecast,
            topology_conn=forecast,
            observation_conn=observations,
            decision_time=datetime(
                2026,
                6,
                9,
                12,
                tzinfo=timezone.utc,
            ),
            max_age=timedelta(seconds=30),
            allow_provisional_day0_replacement=True,
        )

    with pytest.raises(
        ValueError,
        match="GLOBAL_DAY0_PROVISIONAL_POSTERIOR_IDENTITY_MISMATCH",
    ):
        adapter._prepare_current_global_probability_family(
            event,
            forecast_conn=forecast,
            topology_conn=forecast,
            observation_conn=observations,
            decision_time=datetime(
                2026,
                6,
                9,
                12,
                tzinfo=timezone.utc,
            ),
            max_age=timedelta(seconds=30),
            allow_provisional_day0_replacement=True,
            entry_authority=False,
        )
    forecast.close()
    observations.close()


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


def test_replacement_reactor_hook_success_status_is_plain_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.data.replacement_forecast_runtime_policy import ReplacementForecastRuntimePolicy
    from src.data.replacement_forecast_switch_decision import ReplacementForecastSwitchDecision
    from src.engine import replacement_forecast_reactor_hook as hook

    monkeypatch.setattr(
        hook,
        "build_replacement_forecast_receipt_provenance",
        lambda **_kwargs: SimpleNamespace(as_dict=lambda: {"runtime_layer": "live"}),
    )
    policy = ReplacementForecastRuntimePolicy(
        status="live",
        reason_codes=("REPLACEMENT_LIVE_ENABLED",),
        live_enabled=True,
        kelly_increase_enabled=False,
        direction_flip_enabled=False,
    )
    switch = ReplacementForecastSwitchDecision(
        status="live",
        reason_codes=("REPLACEMENT_SWITCH_LIVE_ADMITTED",),
        can_read_live_posterior=True,
        can_apply_reactor_hook=True,
        can_initiate_trade=True,
        can_increase_kelly=False,
        can_flip_direction=False,
        readiness_id="ready-1",
    )

    result = hook.apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=switch,
        candidate={
            "baseline_direction": "buy_yes:bin-28",
            "baseline_q_posterior": 0.75,
            "baseline_q_lcb": 0.65,
            "baseline_kelly_fraction": 0.01,
            "candidate_direction": "buy_yes:bin-28",
            "candidate_q_posterior": 0.80,
            "candidate_q_lcb": 0.70,
            "candidate_kelly_fraction": 0.01,
            "market_snapshot_id": "snap-1",
            "condition_id": "cond-28",
            "token_id": "yes-28",
            "decision_time": "2026-06-18T06:05:00+00:00",
        },
        replacement_bundle=SimpleNamespace(
            posterior_id=123,
            product_id="openmeteo_ecmwf_ifs9_bayes_fusion_v1",
            q={"bin-28": 0.80},
        ),
        readiness=object(),
    )

    assert result.status == "live"
    assert result.as_receipt_tag() == {"runtime_layer": "live"}


def test_replacement_0_1_primary_authority_skips_legacy_replacement_hook() -> None:
    replacement_proof = SimpleNamespace(q_source="replacement_0_1")
    replacement_probability_authority_proof = SimpleNamespace(
        q_source=None,
        probability_authority="replacement_0_1",
    )
    baseline_proof = SimpleNamespace(q_source="emos")
    qkernel_selected_proof = SimpleNamespace(
        q_source="emos",
        selection_authority_applied="qkernel_spine",
    )

    assert adapter._replacement_primary_authority_already_applied(replacement_proof) is True
    assert (
        adapter._replacement_primary_authority_already_applied(
            replacement_probability_authority_proof
        )
        is True
    )
    assert adapter._replacement_primary_authority_already_applied(baseline_proof) is False
    assert adapter._replacement_primary_authority_already_applied(qkernel_selected_proof) is True
