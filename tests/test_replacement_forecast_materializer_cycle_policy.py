# Created: 2026-06-10
# Last reused or audited: 2026-08-12
# Authority basis: operator staleness/cycle-physics directive 2026-06-10 (bounded re-materialization
#   staleness gate at materialization, fail-closed; cycle-phase provenance treats all standard
#   00Z/06Z/12Z/18Z cycles as live-eligible synoptic).
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
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest

from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
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
    tradeable_grade_coverage_sql,
)
from src.data.replacement_forecast_materializer import (
    ReplacementForecastMaterializeRequest,
    _prewrite_block_reasons,
    materialize_replacement_forecast_live,
)
from src.state.db import _create_readiness_state
from src.state.schema.v2_schema import apply_canonical_schema


UTC = timezone.utc
_STALE_REASON = "REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_TOO_STALE"


def test_current_evidence_semantics_is_probability_identity_and_coverage() -> None:
    current = {
        "q_lcb_basis": "fused_center_bootstrap_p05",
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                "shape_lag_hours": 0.0,
                "source_cycle_time": "2026-06-07T12:00:00+00:00",
                "stale_shape_reused": False,
                "translation_applied": False,
            }
        }
    }
    stale = {
        "bayes_precision_fusion": {
            "current_evidence_shape": {"semantics_revision": "older-law"}
        }
    }
    stale_reused = {
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "semantics_revision": STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
                "shape_lag_hours": 6.0,
                "source_cycle_time": "2026-06-07T06:00:00+00:00",
                "stale_shape_reused": True,
                "translation_applied": False,
            }
        }
    }
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
    assert current_evidence_shape_has_entry_authority(current) is True
    assert current_evidence_shape_has_entry_authority(stale_reused) is True

    clause = tradeable_grade_coverage_sql(
        posterior_columns={"q_lcb_json", "q_ucb_json", "provenance_json"},
        decision_time=datetime(2026, 6, 7, 12, tzinfo=UTC),
        alias="p.",
    )
    assert "current_evidence_shape.semantics_revision" in clause
    assert "current_evidence_shape.stale_shape_reused" in clause
    assert "current_evidence_shape.translation_applied" in clause
    assert "current_evidence_shape.shape_lag_hours" in clause
    assert CURRENT_EVIDENCE_SEMANTICS_REVISION in clause
    assert STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION in clause
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
    ] = "2026-06-06T06:00:00+00:00"
    stale_over_bound = json.loads(json.dumps(stale_reused))
    stale_over_bound["bayes_precision_fusion"]["current_evidence_shape"][
        "shape_lag_hours"
    ] = REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT + 0.001
    stale_over_bound["bayes_precision_fusion"]["current_evidence_shape"][
        "source_cycle_time"
    ] = "2026-06-06T05:59:56+00:00"
    negative_lag = json.loads(json.dumps(stale_reused))
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
    ] = "2026-06-07T12:00:00"
    future_cycle = json.loads(json.dumps(current))
    future_cycle["bayes_precision_fusion"]["current_evidence_shape"][
        "source_cycle_time"
    ] = "2026-06-07T12:00:01+00:00"
    old_cycle = json.loads(json.dumps(current))
    old_cycle["bayes_precision_fusion"]["current_evidence_shape"][
        "source_cycle_time"
    ] = "2026-06-06T05:59:59+00:00"
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
    assert is_tradeable(stale_reused) is True
    assert is_tradeable(stale_at_bound) is True
    assert current_evidence_shape_has_entry_authority(stale_at_bound) is True
    assert is_tradeable(stale_over_bound) is False
    assert current_evidence_shape_has_entry_authority(stale_over_bound) is False
    assert is_tradeable(negative_lag) is False
    assert current_evidence_shape_has_entry_authority(negative_lag) is False
    assert is_tradeable(missing_lag) is False
    assert current_evidence_shape_has_entry_authority(missing_lag) is False
    for invalid_cycle in (missing_cycle, naive_cycle, future_cycle, old_cycle):
        assert is_tradeable(invalid_cycle) is False
    assert current_evidence_shape_has_entry_authority(missing_cycle) is False
    assert current_evidence_shape_has_held_authority(naive_cycle) is False
    for malformed_stale in (
        stale_missing_flag,
        stale_translated,
        stale_wrong_revision,
    ):
        assert is_tradeable(malformed_stale) is False
        assert current_evidence_shape_has_entry_authority(malformed_stale) is False
    for malformed in malformed_shapes:
        assert is_tradeable(malformed) is False
        assert current_evidence_shape_has_entry_authority(malformed) is False

    for nonfinite_lag in (float("nan"), float("inf"), float("-inf")):
        malformed = json.loads(json.dumps(stale_reused))
        malformed["bayes_precision_fusion"]["current_evidence_shape"][
            "shape_lag_hours"
        ] = nonfinite_lag
        assert is_tradeable(malformed) is False
        assert current_evidence_shape_has_entry_authority(malformed) is False
        assert current_evidence_shape_has_held_authority(malformed) is False

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
