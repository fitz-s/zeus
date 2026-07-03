# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator staleness/cycle-physics directive 2026-06-10 (06/18Z intermediate
#   cycles differ in skill/bias from 00/12Z synoptic cycles; de-bias trained ~99% 00Z so
#   intermediate-phase posteriors are excluded from live authority until a settlement-graded
#   comparison licenses them; flag default OFF).
"""Relationship test across the materializer->bundle-reader boundary for CYCLE PHASE.

The invariant being pinned is a CROSS-MODULE property, not a single function's output:
the phase the materializer records in provenance_json.cycle_phase (synoptic for 00/12Z,
intermediate for 06/18Z) must drive the bundle reader's live-admission decision. A synoptic
posterior binds; a legacy intermediate posterior row is BLOCKED for live by default and
only admitted when the operator flag flips. A pre-tag legacy row (no cycle_phase key) must
fall back to the source_cycle_time hour and be classified fail-closed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pytest

import src.data.replacement_forecast_bundle_reader as bundle_reader
from src.data.replacement_forecast_bundle_reader import (
    HIGH_DATA_VERSION,
    PRODUCT_ID,
    SOURCE_ID,
    read_replacement_forecast_bundle,
)
from src.data.replacement_forecast_cycle_policy import classify_cycle_phase
from src.data.replacement_forecast_readiness import (
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.state.schema.v2_schema import apply_canonical_schema


UTC = timezone.utc
_TOPO_HASH = "topo-hash-fixed-001"


@dataclass(frozen=True)
class _Evidence:
    source_run_id: str


@dataclass(frozen=True)
class _BaselineBundle:
    evidence: _Evidence


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    return conn


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=UTC)


def _provenance(*, cycle_phase: str | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "bin_topology_hash": _TOPO_HASH,
        "bin_topology": [
            {
                "bin_id": "warm",
                "lower_c": 20.0,
                "upper_c": 21.0,
                "center_c": 20.5,
                "settlement_step_c": 1.0,
                "display_unit": "C",
                "settlement_unit": "C",
                "rounding_rule": "wmo_half_up",
            }
        ],
    }
    # cycle_phase=None models a legacy pre-tag posterior (the key is simply absent).
    if cycle_phase is not None:
        payload["cycle_phase"] = cycle_phase
    return payload


def _insert_posterior(
    conn: sqlite3.Connection,
    *,
    source_cycle_time: datetime,
    cycle_phase: str | None,
) -> int:
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, posterior_method,
            dependency_source_run_ids_json, provenance_json,
            trade_authority_status, training_allowed,
            bin_topology_hash, posterior_identity_hash, dependency_hash,
            posterior_config_hash, q_ucb_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE_ID,
            PRODUCT_ID,
            HIGH_DATA_VERSION,
            "Shanghai",
            "2026-06-07",
            "high",
            source_cycle_time.isoformat(),
            _dt(6, 11).isoformat(),
            _dt(6, 11, 30).isoformat(),
            json.dumps({"cold": 0.2, "warm": 0.8}),
            json.dumps({"cold": 0.1, "warm": 0.7}),
            "openmeteo_ifs9_aifs_sampled_2t_soft_anchor",
            json.dumps(
                {
                    "baseline_b0": "b0-run",
                    "aifs_sampled_2t": "aifs-run",
                    "openmeteo_ifs9_anchor": "om9-run",
                }
            ),
            json.dumps(_provenance(cycle_phase=cycle_phase)),
            "BLOCKED",
            0,
            _TOPO_HASH,
            "pid-hash",
            "dep-hash",
            "cfg-hash",
            None,
        ),
    )
    return int(conn.execute("SELECT posterior_id FROM forecast_posteriors").fetchone()[0])


def _readiness(*, posterior_id: int):
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id="b0-run",
            source_available_at=_dt(6, 0),
        ),
        ReplacementForecastDependency(
            role="aifs_sampled_2t",
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            source_run_id="aifs-run",
            source_available_at=_dt(6, 0),
            artifact_id=11,
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id="om9-run",
            source_available_at=_dt(6, 0),
            anchor_id=22,
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            data_version=HIGH_DATA_VERSION,
            source_run_id="posterior-run",
            source_available_at=_dt(6, 0),
            posterior_id=posterior_id,
        ),
    )
    return build_replacement_forecast_readiness(
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 11),
        computed_at=_dt(6, 11),
        expires_at=_dt(6, 23),
        dependencies=dependencies,
    )


def _read(conn: sqlite3.Connection, posterior_id: int):
    return read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),
        current_bin_topology_hash=_TOPO_HASH,
    )


def test_synoptic_phase_binds_live() -> None:
    """A 12Z synoptic posterior (within the freshness bound) binds for live authority."""
    conn = _conn()
    posterior_id = _insert_posterior(conn, source_cycle_time=_dt(6, 12), cycle_phase="synoptic")
    result = _read(conn, posterior_id)
    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"


def test_intermediate_phase_blocked_live_by_default() -> None:
    """A tagged 18Z legacy posterior row is not live-admissible when the flag is default OFF."""
    conn = _conn()
    posterior_id = _insert_posterior(conn, source_cycle_time=_dt(6, 6), cycle_phase="intermediate")
    result = _read(conn, posterior_id)
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_0_1_LIVE_AUTHORITY_INTERMEDIATE_CYCLE_UNLICENSED"


def test_intermediate_phase_admitted_when_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flipping the operator flag promotes intermediate-phase posteriors to live-eligible."""
    conn = _conn()
    posterior_id = _insert_posterior(conn, source_cycle_time=_dt(6, 6), cycle_phase="intermediate")
    monkeypatch.setattr(
        bundle_reader,
        "_replacement_intermediate_cycle_live_admission_enabled",
        lambda: True,
    )
    result = _read(conn, posterior_id)
    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"


def test_legacy_untagged_row_classified_by_source_cycle_hour() -> None:
    """A pre-tag posterior (no cycle_phase key) falls back to the source_cycle_time hour.

    The no-leak fallback must be fail-closed: a 06Z cycle with no provenance tag is still
    classified intermediate and blocked, exactly as classify_cycle_phase would label it.
    """
    conn = _conn()
    posterior_id = _insert_posterior(conn, source_cycle_time=_dt(6, 6), cycle_phase=None)
    assert classify_cycle_phase(_dt(6, 6)) == "intermediate"
    result = _read(conn, posterior_id)
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_0_1_LIVE_AUTHORITY_INTERMEDIATE_CYCLE_UNLICENSED"
