# Purpose: Verify forecast-cycle eligibility, coverage and current-carrier reseeding.
# Reuse: Run when changing posterior cycle authority or seed coverage and drain rules.
# Created: 2026-06-10
# Last reused or audited: 2026-09-30
# Lifecycle: created=2026-06-10; last_reviewed=2026-09-30; last_reused=2026-09-30
# Authority basis: operator staleness/cycle-physics directive 2026-06-10 (bounded re-materialization
#   staleness gate at materialization, fail-closed; cycle-phase provenance treats all standard
#   00Z/06Z/12Z/18Z cycles as live-eligible synoptic); 2026-08-19 causal
#   capital evidence retirement of cross-clock stale ENS probability authority.
"""Relationship tests across the materializer's input->DB-write boundary for cycle policy.

Two cross-module invariants are pinned here (Fitz: relationship tests, not function tests):

  1. BOUNDED STALENESS — when (computed_at - source_cycle_time) exceeds the shared horizon,
     the materializer must REFUSE to write a posterior (no re-stamp of a too-old cycle into
     a fresh-TTL "current" input). Within the bound, re-stamping the SAME cycle is allowed.
     This is the fail-closed gate at materialization that complements the live-admission gate;
     the same constant (replacement_forecast_cycle_policy) drives both so they cannot drift.

  2. CYCLE-PHASE TAG — all standard 00Z/06Z/12Z/18Z cycles carry
     provenance_json.cycle_phase == "synoptic"; phase is provenance and must not downgrade
     06Z/18Z rows.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.contracts.ensemble_snapshot_provenance import GRID_SURFACE_EVIDENCE_REVISION
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
    REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT,
    STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
    classify_cycle_phase,
    current_evidence_shape_has_entry_authority,
    current_evidence_shape_has_held_authority,
    current_evidence_shape_semantics_mismatch,
    current_evidence_shape_source_cycle_time,
    tradeable_grade_coverage_sql,
)
from src.data.replacement_forecast_materializer import (
    ReplacementForecastMaterializeRequest,
    _current_evidence_shape_from_values,
    _prewrite_block_reasons,
    materialize_replacement_forecast_live,
)
from src.state.db import _create_readiness_state
from src.state.schema.v2_schema import apply_canonical_schema
from tests.test_replacement_forecast_materializer import _hko_source_surface, _hko_native_surfaces, _hko_precision_guard, _hko_dt
from tests.test_replacement_forecast_materializer import _conn as _hko_canonical_conn, _hko_request, _install_hko_live_fusion

pytestmark = pytest.mark.usefixtures("_hko_source_surface")


UTC = timezone.utc
_STALE_REASON = "REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_TOO_STALE"
_SURFACE_HASH = hashlib.sha256(b"selected-land-cell-proof").hexdigest()


def _surface_identity(*, decision_at=None) -> dict:
    from src.data.replacement_forecast_materializer import _bind_provider_geometry_identity
    shape = _current_evidence_shape_from_values(snapshot_id=17, source_cycle_time=_hko_dt(6).isoformat(),
        source_available_at=_hko_dt(7).isoformat(), members_c=tuple(20+n*.05 for n in range(51)),
        provider_values_c={"ecmwf_ifs": 21., "icon_global": 22.}, provider_weights={"ecmwf_ifs": .6, "icon_global": .4},
        provider_cycles=dict.fromkeys(("ecmwf_ifs", "icon_global"), _hko_dt(6).isoformat()), center_c=21.5)
    bound = _bind_provider_geometry_identity(shape, {}, anchor_metadata=_hko_precision_guard().metadata,
                                            decision_at=decision_at or _hko_dt(7))
    return {
        "grid_surface_evidence_revision": GRID_SURFACE_EVIDENCE_REVISION,
        "grid_surface_evidence_identity_hash": _SURFACE_HASH,
        "provider_geometry_evidence": bound.provider_geometry_evidence,
        "provider_geometry_identity_hash": bound.provider_geometry_identity_hash,
        "provider_geometry_audit": bound.provider_geometry_audit,
    }


def _licensed_current_context(monkeypatch):
    """Controlled math/ENS; normal same-DB body/native/ground/anchor evidence."""
    from src.data.station_ground_evidence import forecast_db_from_connection
    conn = _hko_canonical_conn()
    request = _install_hko_live_fusion(monkeypatch, conn=conn,
        request=_hko_request(source_cycle_time=_hko_dt(6), computed_at=_hko_dt(12), expires_at=_hko_dt(14)))
    result = materialize_replacement_forecast_live(conn, request)
    assert result.ok, result.reason_codes
    conn.commit()
    provenance = json.loads(conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_id=?",
        (result.posterior_id,)).fetchone()[0])
    scope = dict(materialized_at=request.computed_at, city=request.city,
        target_date=request.target_date.isoformat(), metric=request.temperature_metric,
        anchor_id=result.anchor_id, forecast_db=forecast_db_from_connection(conn))
    assert current_evidence_shape_has_entry_authority(provenance, **scope)
    assert current_evidence_shape_has_held_authority(provenance, **scope)
    return conn, request, provenance, scope


def test_entry_held_and_sql_coverage_require_same_land_grid_identity(monkeypatch) -> None:
    physical, request, baseline, scope = _licensed_current_context(monkeypatch)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE posterior (q_lcb_json TEXT, q_ucb_json TEXT, provenance_json TEXT)")
    coverage = tradeable_grade_coverage_sql(
        posterior_columns={"q_lcb_json", "q_ucb_json", "provenance_json"},
        decision_time=request.computed_at,
    )
    for changed, expected in (
        ({}, True),
        ({"grid_surface_evidence_identity_hash": "a" * 63}, False),
        ({"grid_surface_evidence_identity_hash": "z" * 64}, False),
        ({"grid_surface_evidence_revision": "old-grid"}, False),
        ({"semantics_revision": "ensemble_center_scenarios_v5"}, False),
    ):
        provenance = json.loads(json.dumps(baseline))
        provenance["bayes_precision_fusion"]["current_evidence_shape"].update(changed)
        assert current_evidence_shape_has_entry_authority(provenance, **scope) is expected
        assert current_evidence_shape_has_held_authority(provenance, **scope) is expected
        conn.execute("DELETE FROM posterior")
        conn.execute("INSERT INTO posterior VALUES ('{}', '{}', ?)", (json.dumps(provenance),))
        assert bool(conn.execute(f"SELECT count(*) FROM posterior WHERE 1=1 {coverage}").fetchone()[0]) is expected
    conn.close()
    physical.close()


def test_selected_land_cell_changes_current_shape_hash_without_changing_math(monkeypatch) -> None:
    from src.data import replacement_forecast_materializer as materializer
    from src.data.replacement_current_value_serving import read_current_instrument_values
    conn, request, baseline, scope = _licensed_current_context(monkeypatch)
    served = read_current_instrument_values(conn, city=request.city, metric=request.temperature_metric,
        target_date=request.target_date.isoformat(), source_cycle_time_iso=request.source_cycle_time.isoformat(),
        decision_time_iso=request.computed_at.isoformat())
    actual = materializer._replacement_bayes_precision_fusion_override()
    # The values are the already-licensed body's controlled physical inputs;
    # the original purpose is math invariance under geometry identity, not .6/.4.
    inputs = {
        "snapshot_id": 17,
        "source_cycle_time": request.source_cycle_time.isoformat(),
        "source_available_at": actual.current_evidence_shape["source_available_at"],
        "members_c": actual.current_evidence_members_c,
        "provider_values_c": {model:value.value_c for model,value in served.items()},
        "provider_weights": dict.fromkeys(served, .5),
        "provider_cycles": dict.fromkeys(served, request.source_cycle_time.isoformat()),
        "center_c": actual.anchor_value_c,
    }
    no_geometry = _current_evidence_shape_from_values(**inputs)
    first = _current_evidence_shape_from_values(**inputs,
        grid_surface_evidence_revision=GRID_SURFACE_EVIDENCE_REVISION,
        grid_surface_evidence_identity_hash="a" * 64)
    second = _current_evidence_shape_from_values(**inputs,
        grid_surface_evidence_revision=GRID_SURFACE_EVIDENCE_REVISION,
        grid_surface_evidence_identity_hash="b" * 64)
    from src.data.replacement_forecast_materializer import _bind_provider_geometry_identity
    audit = baseline["bayes_precision_fusion"]["current_evidence_shape"]["provider_geometry_audit"]
    binding = dict(anchor_metadata=request.openmeteo_precision_guard.metadata, decision_at=request.computed_at,
        station_ground_evidence=audit["anchor_station_ground"], anchor_raw_artifact=audit["anchor_raw_artifact"],
        station_ground_target_coverage=audit["anchor_station_ground_target_coverage"])
    first = _bind_provider_geometry_identity(first, served, **binding)
    second = _bind_provider_geometry_identity(second, served, **binding)
    assert first.predictive_sigma_c == second.predictive_sigma_c == no_geometry.predictive_sigma_c
    assert len({first.shape_hash, second.shape_hash, no_geometry.shape_hash}) == 3
    for candidate, expected in ((first, True), (no_geometry, False)):
        provenance = json.loads(json.dumps(baseline))
        provenance["bayes_precision_fusion"]["current_evidence_shape"] = candidate.as_payload()
        assert current_evidence_shape_has_entry_authority(provenance, **scope) is expected
    conn.close()


@pytest.mark.parametrize(
    "cycle_text",
    (
        "2026-06-07T12:00:00+00",
        "2026-06-07T12:00:00+0000",
        "2026-06-07T12:00:00+00:99",
        "2026-06-07T12:00:00",
    ),
)
def test_shape_cycle_timestamp_uses_one_strict_aware_grammar(cycle_text: str) -> None:
    provenance = {
        "bayes_precision_fusion": {
            "current_evidence_shape": {"source_cycle_time": cycle_text}
        }
    }

    assert current_evidence_shape_source_cycle_time(provenance) is None


@pytest.mark.parametrize(
    "cycle_text",
    (
        "2026-06-07T12:00:00Z",
        "2026-06-07T12:00:00+00:00",
        "2026-06-07T12:00:00.123456-05:00",
    ),
)
def test_shape_cycle_timestamp_accepts_canonical_aware_iso(cycle_text: str) -> None:
    provenance = {
        "bayes_precision_fusion": {
            "current_evidence_shape": {"source_cycle_time": cycle_text}
        }
    }

    assert current_evidence_shape_source_cycle_time(provenance) is not None


def test_current_evidence_semantics_is_probability_identity_and_coverage(monkeypatch) -> None:
    physical, request, current, scope = _licensed_current_context(monkeypatch)
    current["bayes_precision_fusion"]["current_evidence_shape"]["stale_shape_reused"] = False
    stale = {
        "bayes_precision_fusion": {
            "current_evidence_shape": {"semantics_revision": "older-law"}
        }
    }
    stale_reused = json.loads(json.dumps(current))
    stale_reused["bayes_precision_fusion"]["current_evidence_shape"].update(
        semantics_revision=STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
        shape_lag_hours=6.0, source_cycle_time="2026-09-30T00:00:00+00:00", stale_shape_reused=True)
    inconsistent_transport = {
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                "translation_applied": True,
                "shape_lag_hours": 6.0,
            }
        }
    }
    ambiguous_v2_same_cycle = {
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "semantics_revision": "ensemble_center_scenarios_v2",
            }
        }
    }
    ambiguous_v2_transport = {
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "semantics_revision": "ensemble_anomaly_transport_v2",
                "translation_applied": True,
                "shape_lag_hours": 6.0,
            }
        }
    }

    assert current_evidence_shape_semantics_mismatch(current) is False
    assert current_evidence_shape_semantics_mismatch(stale_reused) is False
    assert current_evidence_shape_semantics_mismatch(inconsistent_transport) is True
    assert current_evidence_shape_semantics_mismatch(ambiguous_v2_same_cycle) is True
    assert current_evidence_shape_semantics_mismatch(ambiguous_v2_transport) is True
    assert current_evidence_shape_semantics_mismatch(stale) is True
    assert current_evidence_shape_semantics_mismatch({}) is False
    assert current_evidence_shape_has_entry_authority(current, **scope) is True
    assert current_evidence_shape_has_held_authority(current, **scope) is True
    for proof in ({}, {"grid_surface_evidence_identity_hash": "a" * 63},
                  {"grid_surface_evidence_revision": "old-mask"}):
        missing_proof = json.loads(json.dumps(current))
        missing_proof["bayes_precision_fusion"]["current_evidence_shape"].update(proof)
        if not proof:
            missing_proof["bayes_precision_fusion"]["current_evidence_shape"].pop("grid_surface_evidence_identity_hash")
        assert current_evidence_shape_has_entry_authority(missing_proof, **scope) is False
        assert current_evidence_shape_has_held_authority(missing_proof, **scope) is False
    assert current_evidence_shape_has_entry_authority(stale_reused, **scope) is False
    assert current_evidence_shape_has_held_authority(stale_reused, **scope) is False

    clause = tradeable_grade_coverage_sql(
        posterior_columns={"q_lcb_json", "q_ucb_json", "provenance_json"},
        decision_time=_hko_dt(12),
        alias="p.",
    )
    assert "current_evidence_shape.semantics_revision" in clause
    assert "current_evidence_shape.stale_shape_reused" in clause
    assert "current_evidence_shape.translation_applied" in clause
    assert "current_evidence_shape.shape_lag_hours" in clause
    assert CURRENT_EVIDENCE_SEMANTICS_REVISION in clause
    assert STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION not in clause
    assert "ensemble_center_scenarios_v2" not in clause
    assert "ensemble_anomaly_transport_v2" not in clause

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE posterior (q_lcb_json TEXT, q_ucb_json TEXT, provenance_json TEXT)"
    )

    def is_tradeable(provenance: dict) -> bool:
        conn.execute("DELETE FROM posterior")
        conn.execute(
            "INSERT INTO posterior VALUES (?, ?, ?)",
            ("{}", "{}", json.dumps(provenance)),
        )
        row = conn.execute(
            f"SELECT COUNT(*) FROM posterior p WHERE 1 = 1 {clause}"
        ).fetchone()
        return bool(row[0])

    stale_reused["q_lcb_basis"] = "fused_center_bootstrap_p05"
    missing_lag = json.loads(json.dumps(current))
    del missing_lag["bayes_precision_fusion"]["current_evidence_shape"]["shape_lag_hours"]
    omitted_stale = json.loads(json.dumps(current))
    del omitted_stale["bayes_precision_fusion"]["current_evidence_shape"]["stale_shape_reused"]
    stale_missing_flag = json.loads(json.dumps(stale_reused))
    del stale_missing_flag["bayes_precision_fusion"]["current_evidence_shape"][
        "stale_shape_reused"
    ]
    stale_translated = json.loads(json.dumps(stale_reused))
    stale_translated["bayes_precision_fusion"]["current_evidence_shape"][
        "translation_applied"
    ] = True
    stale_wrong_revision = json.loads(json.dumps(stale_reused))
    stale_wrong_revision["bayes_precision_fusion"]["current_evidence_shape"][
        "semantics_revision"
    ] = CURRENT_EVIDENCE_SEMANTICS_REVISION
    stale_at_bound = json.loads(json.dumps(stale_reused))
    stale_at_bound["bayes_precision_fusion"]["current_evidence_shape"][
        "shape_lag_hours"
    ] = REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT
    stale_at_bound["bayes_precision_fusion"]["current_evidence_shape"][
        "source_cycle_time"
    ] = "2026-09-29T06:00:00+00:00"
    stale_over_bound = json.loads(json.dumps(stale_reused))
    stale_over_bound["bayes_precision_fusion"]["current_evidence_shape"][
        "shape_lag_hours"
    ] = REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT + 0.001
    stale_over_bound["bayes_precision_fusion"]["current_evidence_shape"][
        "source_cycle_time"
    ] = "2026-09-29T05:59:56+00:00"
    negative_lag = json.loads(json.dumps(current))
    negative_lag["bayes_precision_fusion"]["current_evidence_shape"][
        "shape_lag_hours"
    ] = -0.001
    missing_cycle = json.loads(json.dumps(current))
    del missing_cycle["bayes_precision_fusion"]["current_evidence_shape"][
        "source_cycle_time"
    ]
    naive_cycle = json.loads(json.dumps(current))
    naive_cycle["bayes_precision_fusion"]["current_evidence_shape"][
        "source_cycle_time"
    ] = "2026-09-30T12:00:00"
    future_cycle = json.loads(json.dumps(current))
    future_cycle["bayes_precision_fusion"]["current_evidence_shape"][
        "source_cycle_time"
    ] = "2026-09-30T12:00:01+00:00"
    old_cycle = json.loads(json.dumps(current))
    old_cycle["bayes_precision_fusion"]["current_evidence_shape"][
        "source_cycle_time"
    ] = "2026-09-29T05:59:59+00:00"
    malformed_shapes = []
    for field, value in (
        ("shape_lag_hours", False),
        ("shape_lag_hours", "0"),
        ("stale_shape_reused", 0),
        ("stale_shape_reused", "false"),
    ):
        malformed = json.loads(json.dumps(current))
        malformed["bayes_precision_fusion"]["current_evidence_shape"][field] = value
        malformed_shapes.append(malformed)
    assert is_tradeable(current) is True
    assert is_tradeable(omitted_stale) is True
    assert is_tradeable(stale_reused) is False
    assert is_tradeable(stale_at_bound) is False
    assert current_evidence_shape_has_entry_authority(stale_at_bound, **scope) is False
    assert is_tradeable(stale_over_bound) is False
    assert current_evidence_shape_has_entry_authority(stale_over_bound, **scope) is False
    assert is_tradeable(negative_lag) is False
    assert current_evidence_shape_has_entry_authority(negative_lag, **scope) is False
    assert is_tradeable(missing_lag) is False
    assert current_evidence_shape_has_entry_authority(missing_lag, **scope) is False
    for invalid_cycle in (missing_cycle, naive_cycle, future_cycle, old_cycle):
        assert is_tradeable(invalid_cycle) is False
    assert current_evidence_shape_has_entry_authority(missing_cycle, **scope) is False
    assert current_evidence_shape_has_held_authority(naive_cycle, **scope) is False
    for malformed_stale in (
        stale_missing_flag,
        stale_translated,
        stale_wrong_revision,
    ):
        assert is_tradeable(malformed_stale) is False
        assert current_evidence_shape_has_entry_authority(malformed_stale, **scope) is False
    for malformed in malformed_shapes:
        assert is_tradeable(malformed) is False
        assert current_evidence_shape_has_entry_authority(malformed, **scope) is False

    for nonfinite_lag in (float("nan"), float("inf"), float("-inf")):
        malformed = json.loads(json.dumps(current))
        malformed["bayes_precision_fusion"]["current_evidence_shape"][
            "shape_lag_hours"
        ] = nonfinite_lag
        assert is_tradeable(malformed) is False
        assert current_evidence_shape_has_entry_authority(malformed, **scope) is False
        assert current_evidence_shape_has_held_authority(malformed, **scope) is False

    conn.execute("DELETE FROM posterior")
    conn.execute(
        "INSERT INTO posterior VALUES (?, ?, ?)",
        ("{}", "{}", "{malformed-json"),
    )
    assert conn.execute(
        f"SELECT COUNT(*) FROM posterior p WHERE 1 = 1 {clause}"
    ).fetchone()[0] == 0

    missing_provenance_clause = tradeable_grade_coverage_sql(
        posterior_columns={"q_lcb_json", "q_ucb_json"},
        decision_time=datetime(2026, 6, 7, 12, tzinfo=UTC),
        alias="p.",
    )
    assert "AND 0 = 1" in missing_provenance_clause
    conn.close()
    physical.close()


@dataclass(frozen=True)
class _TemperatureBin:
    bin_id: str
    lower_c: float | None = None
    upper_c: float | None = None
    center_c: float | None = None
    display_unit: str = "C"
    settlement_unit: str = "C"
    rounding_rule: str = "wmo_half_up"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    _create_readiness_state(conn)
    return conn


def _anchor(*, cycle: datetime) -> OpenMeteoIfs9LocalDayAnchor:
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 7),
        high_c=27.0,
        low_c=18.5,
        sample_count=4,
        contributing_local_times=(
            datetime(2026, 6, 7, 0, tzinfo=UTC),
            datetime(2026, 6, 7, 6, tzinfo=UTC),
            datetime(2026, 6, 7, 12, tzinfo=UTC),
            datetime(2026, 6, 7, 18, tzinfo=UTC),
        ),
        contributing_valid_times_utc=(
            datetime(2026, 6, 6, 16, tzinfo=UTC),
            datetime(2026, 6, 6, 22, tzinfo=UTC),
            datetime(2026, 6, 7, 4, tzinfo=UTC),
            datetime(2026, 6, 7, 10, tzinfo=UTC),
        ),
        source_cycle_time=cycle,
    )


def _precision_guard():
    return evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(
            city="Shanghai",
            station_id="ZSSS",
            city_lat=31.2304,
            city_lon=121.4737,
            station_lat=31.1979,
            station_lon=121.3363,
            requested_lat=31.1979,
            requested_lon=121.3363,
            requested_coordinate_precision_decimals=4,
            nearest_grid_lat=31.2,
            nearest_grid_lon=121.3,
            nearest_grid_distance_km=3.5,
            native_grid="openmeteo_ecmwf_ifs_9km",
            delivery_grid_resolution="0p1",
            interpolation_method="nearest_gridpoint",
            endpoint_mode="hourly_zeus_aggregated",
            local_day_start_utc=datetime(2026, 6, 6, 16, tzinfo=UTC),
            local_day_end_utc=datetime(2026, 6, 7, 16, tzinfo=UTC),
            timezone_name="Asia/Shanghai",
            target_local_date=date(2026, 6, 7),
            temperature_unit="C",
            anchor_sigma_c=3.0,
            grid_elevation_m=4.0,
            station_elevation_m=3.0,
            land_sea_mask="land",
            city_class="flat_inland",
            station_mapping_policy="settlement_station",
        )
    )


def _bins() -> tuple[_TemperatureBin, ...]:
    return (
        _TemperatureBin("cool", upper_c=20.0, center_c=19.0),
        _TemperatureBin("warm", lower_c=21.0, upper_c=30.0),
        _TemperatureBin("hot", lower_c=31.0, center_c=32.0),
    )


def _request(*, cycle: datetime, computed_at: datetime) -> ReplacementForecastMaterializeRequest:
    return ReplacementForecastMaterializeRequest(
        city="Shanghai",
        city_id="Shanghai",
        city_timezone="Asia/Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        baseline_source_run_id="b0-run",
        baseline_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        baseline_source_available_at=computed_at - timedelta(hours=2),
        openmeteo_anchor=_anchor(cycle=cycle),
        openmeteo_source_run_id="om9-run",
        openmeteo_source_available_at=computed_at - timedelta(hours=1),
        bins=_bins(),
        source_cycle_time=cycle,
        computed_at=computed_at,
        expires_at=computed_at + timedelta(hours=3),
        openmeteo_precision_guard=_precision_guard(),
    )


def test_day0_carrier_coverage_requires_complete_current_v2_pair() -> None:
    """Old/partial carrier declarations drain through the existing seed loop."""

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE posterior (q_lcb_json TEXT, q_ucb_json TEXT, provenance_json TEXT)"
    )
    base = {
        "q_lcb_basis": "fused_center_bootstrap_p05",
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "shape_lag_hours": 0,
                "translation_applied": False,
                "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                "source_cycle_time": "2026-09-20T00:00:00Z",
                **_surface_identity(),
            }
        },
    }
    clause = tradeable_grade_coverage_sql(
        posterior_columns={"q_lcb_json", "q_ucb_json", "provenance_json"},
        decision_time=datetime(2026, 9, 20, 1, tzinfo=UTC),
    )
    cases = (
        ({}, True),
        ({"q_shape": "day0_remaining_shared_carrier_v1"}, False),
        ({"q_shape": "day0_remaining_shared_carrier_v2"}, False),
        ({"q_shape": "day0_remaining_shared_carrier_v3"}, False),
        ({"day0_remaining_carrier_content_identity": "old-censored-station",
          "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
          "day0_remaining_carrier_station_extreme_providers": [{"model": "hko_fnd"}]}, False),
        *(
            ({"day0_remaining_carrier_content_identity": "typed-content",
              "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
              "day0_remaining_carrier_final_extremes_c": centers,
              "day0_remaining_carrier_station_extreme_providers": [
                  {"forecast_value_c": value} for value in centers
              ] if isinstance(centers, (list, tuple)) else []}, accepted)
            for centers, accepted in (([32.0], True), ([], False),
                                      ([True], False), (["32"], False),
                                      ("invalid", False), ([None], False))
        ),
        ({"day0_remaining_carrier_content_identity": "typed-content",
          "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
          "day0_remaining_carrier_final_extremes_c": [32.0],
          "day0_remaining_carrier_station_extreme_providers": [{"forecast_value_c": 31.0}]}, False),
        ({"day0_remaining_carrier_content_identity": "typed-content",
          "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
          "day0_remaining_carrier_final_extremes_c": [32.0],
          "day0_remaining_carrier_station_extreme_providers": [None]}, False),
        ({"q_shape": "day0_remaining_shared_carrier_v2",
          "day0_remaining_carrier_content_identity": "typed-content",
          "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
          "day0_remaining_carrier_final_extremes_c": [32.0],
          "day0_remaining_carrier_station_extreme_providers": [{"forecast_value_c": 32.0}]}, False),

        ({"q_shape": "fused_day0_fast_residual_likelihood"}, True),
        ({"q_shape": "fused_day0_fast_residual_likelihood",
          "day0_remaining_carrier_content_identity": "content-v2",
          "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
          "day0_remaining_carrier_station_extreme_providers": [],
          "day0_remaining_carrier_final_extremes_c": []}, True),
        ({"q_shape": "fused_day0_fast_residual_likelihood",
          "day0_remaining_carrier_content_identity": "typed-content",
          "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
          "day0_remaining_carrier_final_extremes_c": [32.0],
          "day0_remaining_carrier_station_extreme_providers": [{"forecast_value_c": 32.0}]}, True),
        (
            {
                "day0_remaining_carrier_content_identity": "content-v1",
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_v1",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "content-unknown",
                "day0_remaining_carrier_operator": "unknown",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": 17,
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": ["content-v2"],
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": {"value": "content-v2"},
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": None,
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "   ",
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "content-v2",
                "day0_remaining_carrier_operator": " extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "content-v2",
                "day0_remaining_carrier_operator": 17,
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "content-v2",
                "day0_remaining_carrier_operator": None,
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "content-v2",
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            True,
        ),
    )
    for carrier, expected in cases:
        # Current structural positives must also declare the current center
        # construction; old zero/missing-policy rejection is tested separately.
        if carrier and expected:
            carrier = {**carrier, "day0_remaining_center_policy": "unshifted_live_v1",
                       "day0_remaining_center_bias_c": 0.0}
        conn.execute("DELETE FROM posterior")
        conn.execute(
            "INSERT INTO posterior VALUES (?, ?, ?)",
            ("{\"bin\":0.1}", "{\"bin\":0.2}", json.dumps({**base, **carrier})),
        )
        count = conn.execute(
            f"SELECT count(*) FROM posterior WHERE 1=1 {clause}"
        ).fetchone()[0]
        assert bool(count) is expected, carrier


def test_live_unshifted_policy_python_and_sql_coverage_share_strict_types(monkeypatch) -> None:
    """Same lawful body/geometry; only the carrier construction claim changes."""
    physical, request, baseline, scope = _licensed_current_context(monkeypatch)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE posterior (q_lcb_json TEXT, q_ucb_json TEXT, provenance_json TEXT)")
    coverage = tradeable_grade_coverage_sql(
        posterior_columns={"q_lcb_json", "q_ucb_json", "provenance_json"},
        decision_time=request.computed_at,
    )
    current = {"day0_remaining_center_policy": "unshifted_live_v1",
               "day0_remaining_center_bias_c": 0.0}
    cases = [({}, True), (current, True),
             ({"day0_remaining_center_bias_c": 0.0}, False),
             ({"day0_remaining_center_policy": "unshifted_live_v1"}, False),
             ({**current, "day0_remaining_center_policy": "old-live-policy"}, False)]
    cases.extend(({**current, "day0_remaining_center_bias_c": bias}, accepted)
                 for bias, accepted in ((0, True), (-0.0, True), (None, False),
                                        (True, False), ("0", False),
                                        (float("nan"), False), (float("inf"), False),
                                        (0.5, False)))
    for changed, expected in cases:
        provenance = {**baseline, **changed}
        assert current_evidence_shape_has_entry_authority(provenance, **scope) is expected
        assert current_evidence_shape_has_held_authority(provenance, **scope) is expected
        conn.execute("DELETE FROM posterior")
        conn.execute("INSERT INTO posterior VALUES ('{}', '{}', ?)", (json.dumps(provenance),))
        assert bool(conn.execute(f"SELECT count(*) FROM posterior WHERE 1=1 {coverage}").fetchone()[0]) is expected
    conn.close()
    physical.close()


def test_same_raw_live_policy_transition_retries_without_mtime_churn(tmp_path, monkeypatch) -> None:
    """Real canonical inputs stay immutable; policy alone clears old suppression."""
    from pathlib import Path
    from src.events import day0_authority
    from src.data import replacement_forecast_live_materialization_queue as queue

    physical, request, _, scope = _licensed_current_context(monkeypatch)
    original = physical.execute("SELECT * FROM raw_model_forecasts").fetchall()
    payload = dict(city=request.city, target_date=request.target_date.isoformat(),
                   temperature_metric=request.temperature_metric,
                   source_cycle_time=request.source_cycle_time.isoformat(),
                   computed_at=request.computed_at.isoformat())
    before_payload = dict(payload)
    real_stat = Path.stat
    stable_stats = {}

    def fixed_stat(path, *args, **kwargs):
        if str(path) not in stable_stats:
            stable_stats[str(path)] = real_stat(path, *args, **kwargs)
        return stable_stats[str(path)]

    monkeypatch.setattr(Path, "stat", fixed_stat)
    def fingerprint():
        return queue._blocked_attempt_fingerprint(input_json=tmp_path / "seed.json",
            forecast_db=scope["forecast_db"], payload=payload)
    with monkeypatch.context() as previous_policy:
        previous_policy.setattr(day0_authority, "DAY0_REMAINING_CENTER_POLICY", "old-fitted-live-policy")
        before = fingerprint()
    after = fingerprint()
    assert before is not None and after is not None
    assert before != after == fingerprint()
    assert payload == before_payload
    assert physical.execute("SELECT * FROM raw_model_forecasts").fetchall() == original
    physical.close()


def test_day0_v3_coverage_rejects_malformed_provider_and_infinite_value() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE posterior (q_lcb_json TEXT, q_ucb_json TEXT, provenance_json TEXT)"
    )
    base = {
        "q_lcb_basis": "fused_center_bootstrap_p05",
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "shape_lag_hours": 0,
                "translation_applied": False,
                "stale_shape_reused": False,
                "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                "source_cycle_time": "2026-09-20T00:00:00Z",
                **_surface_identity(),
            }
        },
    }
    clause = tradeable_grade_coverage_sql(
        posterior_columns={"q_lcb_json", "q_ucb_json", "provenance_json"},
        decision_time=datetime(2026, 9, 20, 1, tzinfo=UTC),
    )
    cases = [
        *(
            {
                **base,
                "day0_remaining_carrier_content_identity": "typed-content",
                "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
                "day0_remaining_carrier_final_extremes_c": [32.0],
                "day0_remaining_carrier_station_extreme_providers": [{"forecast_value_c": 32.0}],
                field: "invalid",
            }
            for field in (
                "day0_remaining_carrier_final_extremes_c",
                "day0_remaining_carrier_station_extreme_providers",
            )
        ),
        {
            **base,
            "day0_remaining_carrier_content_identity": "typed-content",
            "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
            "day0_remaining_carrier_final_extremes_c": [32.0],
            "day0_remaining_carrier_station_extreme_providers": ["invalid"],
        },
        {
            **base,
            "day0_remaining_carrier_content_identity": "typed-content",
            "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
            "day0_remaining_carrier_final_extremes_c": [32.0],
            "day0_remaining_carrier_station_extreme_providers": "invalid",
        },
        {
            **base,
            "day0_remaining_carrier_content_identity": "typed-content",
            "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
            "day0_remaining_carrier_final_extremes_c": "invalid",
            "day0_remaining_carrier_station_extreme_providers": [
                {"forecast_value_c": 32.0}
            ],
        },
        json.dumps(
            {
                **base,
                "day0_remaining_carrier_content_identity": "typed-content",
                "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
                "day0_remaining_carrier_final_extremes_c": [999.0],
                "day0_remaining_carrier_station_extreme_providers": [
                    {"forecast_value_c": 999.0}
                ],
            }
        ).replace("999.0", "1e999"),
    ]
    for provenance in cases:
        raw = provenance if isinstance(provenance, str) else json.dumps(provenance)
        conn.execute("DELETE FROM posterior")
        conn.execute(
            "INSERT INTO posterior VALUES (?, ?, ?)",
            ('{"bin":0.1}', '{"bin":0.2}', raw),
        )
        assert conn.execute(
            f"SELECT count(*) FROM posterior WHERE 1=1 {clause}"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("legacy_shape_only", (None, "day0_remaining_shared_carrier_v1", "day0_remaining_shared_carrier_v2"))
def test_day0_v1_coverage_drains_seed_and_v2_coverage_stops_reenqueue(tmp_path, monkeypatch, legacy_shape_only) -> None:
    """Legacy-label debt drains through real builder/queue/current certificate.

    The historical name is retained for case identity. This is not a normal
    remaining-V2 kernel test: current zero-observation/full-prior evidence is
    rebuilt; the separate typed-pair SQL test owns V2 structural coverage.
    Forecast/ENS are controlled inputs, not live official GRIB captures.
    """
    from dataclasses import asdict, replace
    from src.data import replacement_forecast_materializer as materializer
    from src.data.replacement_forecast_materialization_request_builder import build_materialize_request_dataclass
    from src.data.station_ground_evidence import forecast_db_from_connection
    from src.contracts.replacement_pipeline_files import DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS
    from src.state.db import init_schema_trade_only
    import src.data.replacement_forecast_live_materialization_queue as queue

    conn = _hko_canonical_conn()
    request = _install_hko_live_fusion(monkeypatch, conn=conn,
        request=_hko_request(source_cycle_time=_hko_dt(12), computed_at=_hko_dt(20), expires_at=_hko_dt(22),
            day0_observation_state=DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS))
    db_path = forecast_db_from_connection(conn)
    assert db_path is not None
    clock = [request.computed_at]
    builtin = sqlite3.connect(":memory:")
    def sqlite_clock(fmt, value):
        return builtin.execute("SELECT strftime(?,?)",
            (fmt, clock[0].isoformat() if value == "now" else value)).fetchone()[0]
    # TEST_ONLY_CONTROLLED_CANONICAL_INSERT_CLOCK and replay clock. Normal
    # private INSERTs/readiness expiry use the explicit fixture analysis cut.
    conn.create_function("strftime", 2, sqlite_clock)
    real_open = queue._queue_read_only_connection
    def open_at_fixture_cut(path, *args, **kwargs):
        opened = real_open(path, *args, **kwargs)
        if str(path) == str(db_path):
            opened.create_function("strftime", 2, sqlite_clock)
        return opened
    monkeypatch.setattr(queue, "_queue_read_only_connection", open_at_fixture_cut)
    payload_path = conn.execute("SELECT artifact_path FROM raw_forecast_artifacts WHERE artifact_id=?",
        (request.anchor_artifact_id,)).fetchone()[0]
    metadata_path = tmp_path / "normal-precision.json"
    metadata_path.write_text(json.dumps(asdict(request.openmeteo_precision_guard.metadata)))
    seed = dict(city=request.city, city_id=request.city_id, city_timezone=request.city_timezone,
        target_date=request.target_date.isoformat(), temperature_metric=request.temperature_metric,
        computed_at=request.computed_at.isoformat(), source_cycle_time=request.source_cycle_time.isoformat(),
        expires_at=request.expires_at.isoformat(), baseline_source_run_id=request.baseline_source_run_id,
        baseline_data_version=request.baseline_data_version,
        baseline_source_available_at=request.baseline_source_available_at.isoformat(),
        openmeteo_source_run_id=request.openmeteo_source_run_id,
        openmeteo_source_available_at=request.openmeteo_source_available_at.isoformat(),
        openmeteo_anchor_artifact_id=request.anchor_artifact_id,
        openmeteo_payload_json=payload_path, precision_metadata_json=str(metadata_path),
        bins=[asdict(item) for item in request.bins], day0_observation_state=request.day0_observation_state)
    # Commit the independently read anchor FK before the private positive control.
    materializer._insert_anchor(conn, request, metric=request.temperature_metric)
    conn.commit()
    conn.execute("SAVEPOINT current_control")
    positive = materialize_replacement_forecast_live(conn, request)
    assert positive.ok, positive.reason_codes
    assert queue._seed_already_covered(forecast_db=db_path, seed=seed, forecast_conn=conn)
    healthy_row = dict(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
        (positive.posterior_id,)).fetchone())
    healthy_readiness = dict(conn.execute("SELECT * FROM readiness_state WHERE readiness_id=?",
        (positive.readiness_id,)).fetchone())
    conn.execute("PRAGMA query_only=OFF")
    conn.execute("ROLLBACK TO current_control")
    conn.execute("RELEASE current_control")
    # TEST_ONLY_SYNTHETIC_EXTERNAL_CONDITION: recreate the historical diagnostic
    # input in this private schema. No READY enum mock and no relabeling repair:
    # its readiness fields came from the normal positive writer above.
    provenance = json.loads(healthy_row["provenance_json"])
    if legacy_shape_only is None:
        provenance.update(day0_remaining_carrier_content_identity="content-v1",
            day0_remaining_carrier_operator="extreme_observed_then_noisy_future_v1")
    else:
        provenance["q_shape"] = legacy_shape_only
    healthy_row["provenance_json"] = json.dumps(provenance)
    def insert_record(table, row):
        # Let canonical generated identity columns reproduce their own values.
        writable = {field[1] for field in conn.execute(f"PRAGMA table_info({table})")}
        row = {key:value for key,value in row.items() if key in writable}
        conn.execute(f"INSERT INTO {table} (" + ",".join(row) + ") VALUES (" +
            ",".join("?" for _ in row) + ")", tuple(row.values()))
    insert_record("forecast_posteriors", healthy_row)
    insert_record("readiness_state", healthy_readiness)
    conn.commit()
    old_id = positive.posterior_id
    old_tuple = tuple(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?", (old_id,)).fetchone())
    raw_inputs = [tuple(row) for row in conn.execute("""SELECT raw_model_forecast_id,model,source_cycle_time,
        source_available_at,captured_at,recorded_at,raw_sha256 FROM raw_model_forecasts ORDER BY raw_model_forecast_id""")]
    assert not queue._seed_already_covered(forecast_db=db_path, seed=seed)
    # A new actual analysis cut produces a new certificate; the old diagnostic
    # tuple stays immutable rather than being restamped to the current law.
    request = _install_hko_live_fusion(monkeypatch, conn=conn,
        request=replace(request, computed_at=request.computed_at+timedelta(minutes=1)))
    clock[0] = request.computed_at
    conn.create_function("strftime", 2, sqlite_clock)
    seed["computed_at"] = request.computed_at.isoformat()
    metadata_path.write_text(json.dumps(asdict(request.openmeteo_precision_guard.metadata)))
    trade_conn = sqlite3.connect(tmp_path / "trade.db")
    init_schema_trade_only(trade_conn)
    trade_conn.commit()
    seed_dir, processed_dir, failed_dir, request_dir = (tmp_path / name for name in ("seeds", "processed", "failed", "requests"))
    seed_dir.mkdir()
    seed_path = seed_dir / "Hong Kong.2026-10-01.high.station-input-revision.1.json"
    seed_path.write_text(json.dumps(seed))
    real_builder = queue.build_replacement_forecast_materialization_request
    calls = []
    def normal_builder(*args, **kwargs):
        calls.append(1)
        return real_builder(*args, **kwargs)
    monkeypatch.setattr(queue, "build_replacement_forecast_materialization_request", normal_builder)
    def drain():
        return queue._prepare_seed_requests_with_connection(seed_dir=seed_dir,
            seed_processed_dir=processed_dir, seed_failed_dir=failed_dir, request_dir=request_dir,
            forecast_db=db_path, forecast_conn=None, trade_conn=trade_conn, limit=1,
            fast_own_clock_station_revision=True)
    processed, failed, reasons = drain()
    assert processed and not failed, (processed, failed, reasons)
    request_path = request_dir / seed_path.name
    assert request_path.is_file()
    assert len(calls) == 1
    built = build_materialize_request_dataclass(json.loads(request_path.read_text()), base_dir=request_dir)
    result = materialize_replacement_forecast_live(conn, built)
    assert result.ok, result.reason_codes
    conn.commit()
    assert result.posterior_id != old_id
    current = json.loads(conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_id=?",
        (result.posterior_id,)).fetchone()[0])
    scope = dict(materialized_at=built.computed_at, city=built.city, target_date=built.target_date.isoformat(),
        metric=built.temperature_metric, anchor_id=result.anchor_id, forecast_db=db_path)
    assert current_evidence_shape_has_entry_authority(current, **scope)
    assert current_evidence_shape_has_held_authority(current, **scope)
    assert [tuple(row) for row in conn.execute("""SELECT raw_model_forecast_id,model,source_cycle_time,
        source_available_at,captured_at,recorded_at,raw_sha256 FROM raw_model_forecasts ORDER BY raw_model_forecast_id""")] == raw_inputs
    assert tuple(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?", (old_id,)).fetchone()) == old_tuple
    assert queue._seed_already_covered(forecast_db=db_path, seed=seed)
    seed_path_v2 = seed_dir / "Hong Kong.2026-10-01.high.station-input-revision.2.json"
    seed_path_v2.write_text(json.dumps(seed))
    processed_v2, failed_v2, reasons_v2 = drain()
    assert processed_v2 and not failed_v2, (processed_v2, failed_v2, reasons_v2)
    assert not (request_dir / seed_path_v2.name).exists()
    assert len(calls) == 1  # Real builder was not called for current coverage.
    conn.close()
    trade_conn.close()
    builtin.close()


def test_prewrite_blocks_when_cycle_older_than_bound() -> None:
    """(computed_at - source_cycle_time) > 30h => the stale reason is present in the prewrite gate."""
    cycle = datetime(2026, 6, 5, 0, tzinfo=UTC)  # 00Z (synoptic) so phase never confounds this
    computed_at = cycle + timedelta(hours=REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT + 2)
    reasons = _prewrite_block_reasons(_request(cycle=cycle, computed_at=computed_at))
    assert _STALE_REASON in reasons


def test_prewrite_blocks_future_cycle() -> None:
    cycle = datetime(2026, 6, 5, 6, tzinfo=UTC)
    computed_at = cycle - timedelta(minutes=1)

    reasons = _prewrite_block_reasons(_request(cycle=cycle, computed_at=computed_at))

    assert _STALE_REASON in reasons


def test_prewrite_allows_re_stamp_within_bound() -> None:
    """Re-stamping the SAME cycle is allowed while within the bound (stale reason ABSENT)."""
    cycle = datetime(2026, 6, 5, 0, tzinfo=UTC)
    computed_at = cycle + timedelta(hours=REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT - 2)
    reasons = _prewrite_block_reasons(_request(cycle=cycle, computed_at=computed_at))
    assert _STALE_REASON not in reasons


def test_materialize_refuses_too_stale_cycle_blocks_all_writes() -> None:
    """The full materializer refuses to write ANY row for an over-bound cycle (fail-closed).

    The prewrite gate runs before any anchor/posterior/readiness INSERT, so an over-bound
    cycle leaves the forecast DB untouched — a too-stale cycle can never be re-stamped at all.
    """
    conn = _conn()
    cycle = datetime(2026, 6, 5, 0, tzinfo=UTC)  # 00Z so the staleness reason is isolated
    computed_at = cycle + timedelta(hours=REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT + 2)
    result = materialize_replacement_forecast_live(conn, _request(cycle=cycle, computed_at=computed_at))
    assert result.ok is False
    assert _STALE_REASON in result.reason_codes
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0


@pytest.mark.parametrize(
    "cycle_hour, expected_phase",
    [(0, "synoptic"), (6, "synoptic"), (12, "synoptic"), (18, "synoptic")],
)
def test_request_cycle_classifies_to_provenance_phase(cycle_hour: int, expected_phase: str) -> None:
    """Producer contract: the phase tag the materializer writes is classify_cycle_phase(source_cycle_time).

    The materializer derives provenance_json.cycle_phase from the request's source_cycle_time via
    classify_cycle_phase (src.data.replacement_forecast_materializer._insert_posterior). This pins
    the producer half of the producer->bundle-reader relationship: 06/18Z requests must not be
    downgraded by phase provenance.
    """
    cycle = datetime(2026, 6, 6, cycle_hour, tzinfo=UTC)
    request = _request(cycle=cycle, computed_at=cycle + timedelta(hours=4))
    assert classify_cycle_phase(request.source_cycle_time) == expected_phase
