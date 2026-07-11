# Created: 2026-06-06
# Last reused/audited: 2026-07-11
# Lifecycle: created=2026-06-06; last_reviewed=2026-07-11; last_reused=2026-07-11
# Purpose: Protect replacement posterior bundle reader no-bypass semantics.
# Reuse: Run before wiring replacement posterior into executable forecast reader or event reactor.
# Authority basis: Operator-directed live replacement forecast bundle reader semantics.
"""Replacement forecast posterior bundle reader tests."""

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
from src.data.replacement_forecast_readiness import LIVE_RUNTIME_LAYER, ReplacementForecastDependency, build_replacement_forecast_readiness
from src.state.schema.v2_schema import apply_canonical_schema


UTC = timezone.utc


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


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _insert_posterior(
    conn: sqlite3.Connection,
    *,
    source_available_at: datetime | None = None,
    computed_at: datetime | None = None,
    training_allowed: int = 0,
    dependency_source_run_ids: dict[str, str] | None = None,
) -> int:
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, q_ucb_json, posterior_method,
            dependency_source_run_ids_json, provenance_json,
            family_id, bin_topology_hash, dependency_hash, posterior_config_hash,
            posterior_identity_hash, runtime_layer, training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE_ID,
            PRODUCT_ID,
            HIGH_DATA_VERSION,
            "Shanghai",
            "2026-06-07",
            "high",
            "2026-06-06T00:00:00+00:00",
            (source_available_at or _dt(3)).isoformat(),
            (computed_at or _dt(3, 5)).isoformat(),
            json.dumps({"cold": 0.2, "warm": 0.8}),
            json.dumps({"cold": 0.1, "warm": 0.7}),
            json.dumps({"cold": 0.3, "warm": 0.9}),
            "openmeteo_ecmwf_ifs9_bayes_fusion",
            json.dumps(
                dependency_source_run_ids
                or {
                    "baseline_b0": "b0-run",
                    "openmeteo_ifs9_anchor": "om9-run",
                }
            ),
            json.dumps({"reader_test": True, "replacement_q_mode": "FUSED_NORMAL_FULL", "bin_topology_hash": "topology-hash"}),
            "Shanghai:2026-06-07:high:topology-hash",
            "topology-hash",
            "dependency-hash",
            "config-hash",
            f"identity-{(computed_at or _dt(3, 5)).isoformat()}-{(source_available_at or _dt(3)).isoformat()}",
            LIVE_RUNTIME_LAYER,
            training_allowed,
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _readiness(*, posterior_id: int, baseline_run_id: str = "b0-run", posterior_available_at: datetime | None = None):
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id=baseline_run_id,
            source_available_at=_dt(2),
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
            source_available_at=posterior_available_at or _dt(3),
            posterior_id=posterior_id,
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


def _insert_raw_model_forecast(
    conn: sqlite3.Connection,
    *,
    model: str,
    source_cycle_time: datetime,
    captured_at: datetime,
    source_available_at: datetime,
    city: str = "Shanghai",
    target_date: str = "2026-06-07",
    metric: str = "high",
) -> None:
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time,
            source_available_at, captured_at, lead_days, forecast_value_c, endpoint,
            coverage_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model,
            city,
            target_date,
            metric,
            source_cycle_time.isoformat(),
            source_available_at.isoformat(),
            captured_at.isoformat(),
            1,
            28.0,
            "single_runs",
            "COVERED",
        ),
    )


def test_replacement_bundle_reader_requires_baseline_executable_bundle() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=None,
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_BASELINE_EXECUTABLE_FORECAST_REQUIRED"


def test_replacement_bundle_reader_returns_posterior_when_b0_and_readiness_match() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"
    assert result.bundle is not None
    assert result.bundle.posterior_id == posterior_id
    assert result.bundle.baseline_source_run_id == "b0-run"
    assert result.bundle.q == pytest.approx({"cold": 0.2, "warm": 0.8})
    assert result.bundle.q_lcb == pytest.approx({"cold": 0.1, "warm": 0.7})
    assert result.bundle.runtime_layer == LIVE_RUNTIME_LAYER


def test_replacement_bundle_reader_binds_to_readiness_posterior_not_latest_scope_row() -> None:
    conn = _conn()
    certified_posterior_id = _insert_posterior(conn, computed_at=_dt(3, 5))
    newer_posterior_id = _insert_posterior(conn, computed_at=_dt(3, 20))

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=certified_posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"
    assert result.bundle is not None
    assert result.bundle.posterior_id == certified_posterior_id
    assert result.bundle.posterior_id != newer_posterior_id


def test_replacement_bundle_reader_blocks_unready_readiness_or_mismatched_ids() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)

    blocked_readiness = _readiness(posterior_id=posterior_id, posterior_available_at=_dt(5))
    blocked = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=blocked_readiness,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert blocked.reason_code == "REPLACEMENT_READINESS_NOT_READY"

    mismatch = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("different-b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert mismatch.reason_code == "REPLACEMENT_BASELINE_READINESS_MISMATCH"

    posterior_mismatch = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id + 100),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert posterior_mismatch.reason_code == "REPLACEMENT_POSTERIOR_READINESS_MISMATCH"


def test_replacement_bundle_reader_blocks_dependency_source_run_drift() -> None:
    conn = _conn()
    openmeteo_drift_id = _insert_posterior(
        conn,
        dependency_source_run_ids={
            "baseline_b0": "b0-run",
            "openmeteo_ifs9_anchor": "wrong-om9-run",
        },
    )

    openmeteo_drift = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=openmeteo_drift_id),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )

    assert openmeteo_drift.reason_code == "REPLACEMENT_DEPENDENCY_SOURCE_RUN_MISMATCH"


def test_replacement_bundle_reader_blocks_missing_or_late_posterior() -> None:
    conn = _conn()
    missing = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=1),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert missing.reason_code == "REPLACEMENT_POSTERIOR_MISSING"

    late_id = _insert_posterior(conn, source_available_at=_dt(5))
    late = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=late_id),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert late.reason_code == "REPLACEMENT_POSTERIOR_AFTER_DECISION_TIME"

    conn = _conn()
    computed_late_id = _insert_posterior(conn, computed_at=_dt(5))
    computed_late = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=computed_late_id),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert computed_late.reason_code == "REPLACEMENT_POSTERIOR_COMPUTED_AFTER_DECISION_TIME"


def test_replacement_bundle_reader_enforce_raw_input_hwm_blocks_stale_serve() -> None:
    """W0.1: when opted in, a raw input newer than the served posterior's source_cycle_time
    must block the read instead of serving the stale posterior."""
    conn = _conn()
    posterior_id = _insert_posterior(conn)  # source_cycle_time = 2026-06-06T00:00:00+00:00
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is False
    assert result.reason_code.startswith("REPLACEMENT_RAW_INPUT_HWM:")
    assert "latest_raw_cycle=2026-06-06T03:00:00+00:00" in result.reason_code
    assert "posterior_cycle=2026-06-06T00:00:00+00:00" in result.reason_code


def test_replacement_bundle_reader_raw_input_hwm_default_is_byte_identical() -> None:
    """W0.1: enforce_raw_input_hwm defaults to False — a caller that never opts in must
    keep serving the SAME posterior even when a newer raw input cycle exists."""
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"
    assert result.bundle is not None
    assert result.bundle.posterior_id == posterior_id


def test_replacement_bundle_reader_enforce_raw_input_hwm_allows_fresh_serve() -> None:
    """W0.1: opting in must not block a posterior that is already the freshest input."""
    conn = _conn()
    posterior_id = _insert_posterior(conn)  # source_cycle_time = 2026-06-06T00:00:00+00:00
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(0),
            captured_at=_dt(0, 5),
            source_available_at=_dt(0, 5),
        )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"


def test_raw_hwm_reuses_bound_posterior_provenance(monkeypatch) -> None:
    import src.data.replacement_forecast_bundle_reader as reader

    conn = _conn()
    posterior_id = _insert_posterior(conn)
    provenance = {
        "reader_test": True,
        "replacement_q_mode": "FUSED_NORMAL_FULL",
        "bin_topology_hash": "topology-hash",
        "bayes_precision_fusion": {
            "used_models": ["ecmwf_ifs", "gfs"],
            "current_value_serving": {
                "ecmwf_ifs": {"raw_model_forecast_id": 1},
                "gfs": {"raw_model_forecast_id": 2},
            },
        },
    }
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), posterior_id),
    )
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(0),
            captured_at=_dt(0, 5),
            source_available_at=_dt(0, 5),
        )
    traced: list[str] = []
    provenance_parses = 0
    original_json_mapping = reader._json_mapping

    def counted_json_mapping(value, *, field_name):
        nonlocal provenance_parses
        if field_name == "provenance_json":
            provenance_parses += 1
        return original_json_mapping(value, field_name=field_name)

    monkeypatch.setattr(reader, "_json_mapping", counted_json_mapping)
    conn.set_trace_callback(traced.append)

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )
    conn.set_trace_callback(None)

    assert result.ok is True
    duplicate_provenance_reads = [
        statement
        for statement in traced
        if "SELECT PROVENANCE_JSON" in statement.upper()
        and "WHERE CITY" in statement.upper()
    ]
    assert duplicate_provenance_reads == []
    assert provenance_parses == 1
