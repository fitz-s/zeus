# Created: 2026-06-07
# Last reused or audited: 2026-06-07
# Authority basis: REAUDIT_0_1.md §2 H3 (expires_at loaded but never compared to decision_time) + §4.
"""H3 antibody — readiness expiry / source-cycle age must be a HARD gate.

Relationship test across the readiness->bundle boundary: a READY posterior whose
``readiness.expires_at < decision_time`` (or whose ``source_cycle_time`` is older
than the operator-configured horizon) must FAIL CLOSED in the bundle reader so
both the live 0.1 path and the legacy hook inherit ONE staleness gate. Trading a
dead/stale forecast as live is the inverse of the zero-trade fault.

The gate lives in ``read_replacement_forecast_bundle`` (the single bundle reader)
so it cannot be bypassed by either consuming path.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pytest

from src.data.replacement_forecast_bundle_reader import (
    HIGH_DATA_VERSION,
    PRODUCT_ID,
    SOURCE_ID,
    read_replacement_forecast_bundle,
)
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


def _provenance() -> dict[str, object]:
    return {
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


def _insert_posterior(
    conn: sqlite3.Connection,
    *,
    source_cycle_time: datetime,
    source_available_at: datetime,
    computed_at: datetime,
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
            source_available_at.isoformat(),
            computed_at.isoformat(),
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
            json.dumps(_provenance()),
            "SHADOW_VETO_ONLY",
            0,
            _TOPO_HASH,
            "pid-hash",
            "dep-hash",
            "cfg-hash",
            None,
        ),
    )
    return int(conn.execute("SELECT posterior_id FROM forecast_posteriors").fetchone()[0])


def _readiness(*, posterior_id: int, computed_at: datetime, expires_at: datetime, decision_time: datetime):
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
        decision_time=decision_time,
        computed_at=computed_at,
        expires_at=expires_at,
        dependencies=dependencies,
    )


def test_bundle_reader_serves_expired_readiness_with_brand() -> None:
    """OPERATOR LAW (2026-06-11, "没有新的就用老的"): expired readiness BRANDS, never blocks.

    The forecast was computed early and its readiness EXPIRED before the decision
    moment. The reader serves the freshest tradeable row that exists, carrying the
    expiry as a staleness_violations provenance brand (plan:
    docs/evidence/settlement_guard/2026-06-11_serve_freshest_available_plan.md).
    """
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 1),
        computed_at=_dt(6, 1, 30),
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 1),
        expires_at=_dt(6, 2),          # expires at 06-06 02:00 ...
        decision_time=_dt(6, 1),       # readiness built at a fresh decision moment
    )
    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=readiness,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),      # ... but the decision happens at 12:00 (expired)
        current_bin_topology_hash=_TOPO_HASH,
    )
    assert result.ok is True
    violations = (result.bundle.provenance_json or {}).get("staleness_violations") or []
    assert any("READINESS_EXPIRED" in v for v in violations), violations


def test_bundle_reader_serves_stale_source_cycle_with_brand() -> None:
    """A source cycle beyond the staleness bound (60h) serves WITH the age brand.

    OPERATOR LAW: the bound drives the download/re-seed pursuit and the brand —
    never darkness. The served bundle's provenance carries the parseable
    CYCLE_AGE_EXCEEDS_BOUND violation (source_cycle + age_hours).
    """
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(4, 0),   # cycle 60h before decision
        source_available_at=_dt(4, 1),
        computed_at=_dt(4, 1, 30),
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 11),
        expires_at=_dt(6, 23),         # not expired by wall clock
        decision_time=_dt(6, 11),
    )
    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=readiness,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),
        current_bin_topology_hash=_TOPO_HASH,
    )
    assert result.ok is True
    violations = (result.bundle.provenance_json or {}).get("staleness_violations") or []
    assert any("CYCLE_AGE_EXCEEDS_BOUND" in v and "age_hours=60.0" in v for v in violations), violations


def test_bundle_reader_accepts_fresh_readiness() -> None:
    """Fresh forecast (not expired, recent SYNOPTIC cycle) still binds — gate is not over-broad.

    Uses a 12Z (synoptic) cycle so this asserts the STALENESS gate is not over-broad without
    tripping the separate intermediate-cycle (06/18Z) shadow-only gate. The intermediate-phase
    admission behaviour is covered by test_replacement_forecast_cycle_phase_admission.py.
    """
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),   # 00Z synoptic cycle, ~12h before decision (within bound)
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 11),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11),
    )
    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=readiness,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),
        current_bin_topology_hash=_TOPO_HASH,
    )
    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"
    assert result.bundle is not None
    assert result.bundle.posterior_id == posterior_id
