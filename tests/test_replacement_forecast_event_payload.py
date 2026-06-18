# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement shadow event payload provenance without mutating baseline FSR wire format.
# Reuse: Run before wiring replacement shadow/veto payloads into the event reactor.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement forecast event payload provenance tests."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from src.data.replacement_forecast_bundle_reader import HIGH_DATA_VERSION, PRODUCT_ID, SOURCE_ID, ReplacementForecastPosteriorBundle
from src.data.replacement_forecast_event_payload import build_replacement_forecast_event_payload
from src.data.replacement_forecast_readiness import ReplacementForecastDependency, build_replacement_forecast_readiness
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event


UTC = timezone.utc


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _baseline_payload() -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        source_id="ecmwf_open_data",
        source_run_id="b0-run",
        cycle="2026-06-06T00:00:00+00:00",
        track="ens",
        snapshot_id="b0-snapshot",
        snapshot_hash="b0-hash",
        captured_at="2026-06-06T02:00:00+00:00",
        available_at="2026-06-06T02:05:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )


def _bundle() -> ReplacementForecastPosteriorBundle:
    return ReplacementForecastPosteriorBundle(
        posterior_id=77,
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        source_id=SOURCE_ID,
        product_id=PRODUCT_ID,
        data_version=HIGH_DATA_VERSION,
        q={"cool": 0.25, "warm": 0.75},
        q_lcb={"cool": 0.20, "warm": 0.65},
        q_ucb={"cool": 0.30, "warm": 0.85},
        posterior_method="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        source_cycle_time="2026-06-06T00:00:00+00:00",
        source_available_at="2026-06-06T03:00:00+00:00",
        computed_at="2026-06-06T03:05:00+00:00",
        baseline_source_run_id="b0-run",
        dependency_json={"source_run_ids": ["b0-run", "aifs-run", "om9-run"]},
        provenance_json={"test": True},
        trade_authority_status="LIVE_AUTHORITY",
        bin_topology_hash="topology-hash",
        family_id="Shanghai|2026-06-07|high",
    )


def _readiness():
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id="b0-run",
            source_available_at=_dt(2),
        ),
        ReplacementForecastDependency(
            role="aifs_sampled_2t",
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            source_run_id="aifs-run",
            source_available_at=_dt(2),
            artifact_id=11,
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id="om9-run",
            source_available_at=_dt(2),
            anchor_id=22,
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            data_version=HIGH_DATA_VERSION,
            source_run_id="posterior-run",
            source_available_at=_dt(3),
            posterior_id=77,
        ),
    )
    return build_replacement_forecast_readiness(
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        computed_at=_dt(4, 1),
        expires_at=_dt(6),
        dependencies=dependencies,
    )


def test_baseline_forecast_snapshot_payload_is_not_mutated_by_default() -> None:
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Shanghai|2026-06-07|high|b0-run",
        source="forecast_snapshot_ready_trigger",
        observed_at="2026-06-06T02:00:00+00:00",
        available_at="2026-06-06T02:05:00+00:00",
        received_at="2026-06-06T02:06:00+00:00",
        causal_snapshot_id="b0-snapshot",
        payload=_baseline_payload(),
    )

    payload = json.loads(event.payload_json)
    assert "replacement_forecast" not in payload


def test_replacement_live_payload_carries_product_and_dependency_identity() -> None:
    enriched = build_replacement_forecast_event_payload(
        base_payload=_baseline_payload(),
        replacement_bundle=_bundle(),
        readiness=_readiness(),
    )
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Shanghai|2026-06-07|high|b0-run|replacement-live",
        source="replacement_forecast_live_ready",
        observed_at="2026-06-06T03:05:00+00:00",
        available_at="2026-06-06T03:05:00+00:00",
        received_at="2026-06-06T03:06:00+00:00",
        causal_snapshot_id="b0-snapshot",
        payload=enriched.as_dict(),
    )

    payload = json.loads(event.payload_json)
    replacement = payload["replacement_forecast"]
    assert replacement["source_id"] == SOURCE_ID
    assert replacement["product_id"] == PRODUCT_ID
    assert replacement["data_version"] == HIGH_DATA_VERSION
    assert replacement["posterior_id"] == 77
    assert replacement["readiness_status"] == "READY"
    assert replacement["trade_authority_status"] == "LIVE_AUTHORITY"
    assert replacement["dependency_source_run_ids"] == {
        "baseline_b0": "b0-run",
        "openmeteo_ifs9_anchor": "om9-run",
        "soft_anchor_posterior": "posterior-run",
    }
    assert replacement["dependency_object_ids"]["openmeteo_ifs9_anchor"] == {"anchor_id": 22}
    assert replacement["dependency_object_ids"]["soft_anchor_posterior"] == {"posterior_id": 77}
    assert replacement["dependency_diagnostics"] == {
        "missing_roles": [],
        "unavailable_roles": [],
        "blocked_roles": [],
        "identity_mismatch_roles": [],
    }
    assert replacement["authority_limits"] == {
        "can_flip_direction": False,
        "can_increase_kelly": False,
        "can_increase_q_lcb": False,
        "can_initiate_trade": True,
    }


def test_replacement_live_payload_rejects_shadow_bundle_unready_or_mismatched_identity() -> None:
    readiness = _readiness()
    bad_bundle = ReplacementForecastPosteriorBundle(
        **{**_bundle().__dict__, "source_id": "different_source_id"}
    )

    with pytest.raises(ValueError, match="LIVE_AUTHORITY"):
        ReplacementForecastPosteriorBundle(
            **{**_bundle().__dict__, "trade_authority_status": "SHADOW_VETO_ONLY"}
        )

    with pytest.raises(ValueError, match="identity mismatch"):
        build_replacement_forecast_event_payload(
            base_payload=_baseline_payload(),
            replacement_bundle=bad_bundle,
            readiness=readiness,
        )

    blocked_readiness = build_replacement_forecast_readiness(
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(1),
        computed_at=_dt(1, 1),
        expires_at=_dt(6),
        dependencies=(
            ReplacementForecastDependency(
                role="baseline_b0",
                source_id="ecmwf_open_data",
                product_id="ecmwf_opendata_ifs_ens_0p25",
                data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
                source_run_id="b0-run",
                source_available_at=_dt(2),
            ),
        ),
    )
    with pytest.raises(ValueError, match="READY readiness"):
        build_replacement_forecast_event_payload(
            base_payload=_baseline_payload(),
            replacement_bundle=_bundle(),
            readiness=blocked_readiness,
        )
