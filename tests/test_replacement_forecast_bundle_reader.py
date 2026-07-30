# Created: 2026-06-06
# Last reused/audited: 2026-07-29
# Lifecycle: created=2026-06-06; last_reviewed=2026-07-29; last_reused=2026-07-29
# Purpose: Protect replacement posterior bundle reader no-bypass semantics.
# Reuse: Run before wiring replacement posterior into executable forecast reader or event reactor.
# Authority basis: Operator-directed live replacement forecast bundle reader semantics.
"""Replacement forecast posterior bundle reader tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest

import src.data.replacement_forecast_bundle_reader as reader
from src.data.replacement_forecast_bundle_reader import (
    HIGH_DATA_VERSION,
    PRODUCT_ID,
    SOURCE_ID,
    read_replacement_forecast_bundle,
)
from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
    TRADEABLE_GRADE_QLCB_BASIS,
)
from src.data.openmeteo_ecmwf_ifs9_anchor import (
    PRODUCT_ID as OPENMETEO_ANCHOR_PRODUCT_ID,
    SOURCE_ID as OPENMETEO_ANCHOR_SOURCE_ID,
)
from src.data.replacement_forecast_readiness import LIVE_RUNTIME_LAYER, ReplacementForecastDependency, build_replacement_forecast_readiness
from src.data.replacement_input_hwm import (
    _exact_consumed_anchor_artifact_cycle,
    replacement_live_input_lag_reason,
)
from src.state.schema.v2_schema import apply_canonical_schema


UTC = timezone.utc


@dataclass(frozen=True)
class _Evidence:
    source_run_id: str


@dataclass(frozen=True)
class _BaselineBundle:
    evidence: _Evidence


def test_preloaded_market_topology_hash_matches_database_read() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE market_events (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            range_label TEXT,
            outcome TEXT,
            range_low REAL,
            range_high REAL
        )
        """
    )
    rows = [
        {
            "condition_id": "c2",
            "range_label": "72F or above",
            "outcome": None,
            "range_low": 72.0,
            "range_high": None,
        },
        {
            "condition_id": "c0",
            "range_label": "69F or below",
            "outcome": None,
            "range_low": None,
            "range_high": 69.0,
        },
        {
            "condition_id": "c1",
            "range_label": "70-71F",
            "outcome": None,
            "range_low": 70.0,
            "range_high": 71.0,
        },
    ]
    conn.executemany(
        "INSERT INTO market_events VALUES ('Dallas', '2026-07-11', 'high', ?, ?, ?, ?, ?)",
        [
            (
                row["condition_id"],
                row["range_label"],
                row["outcome"],
                row["range_low"],
                row["range_high"],
            )
            for row in rows
        ],
    )

    assert reader.market_bin_topology_hash_from_rows(rows, city="Dallas") == (
        reader._current_market_bin_topology_hash(
            conn,
            city="Dallas",
            target_date="2026-07-11",
            temperature_metric="high",
        )
    )


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    return conn


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _live_provenance() -> dict[str, object]:
    return {
        "reader_test": True,
        "replacement_q_mode": "FUSED_NORMAL_FULL",
        "q_lcb_basis": TRADEABLE_GRADE_QLCB_BASIS,
        "bin_topology_hash": "topology-hash",
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                "shape_lag_hours": 0.0,
                "stale_shape_reused": False,
                "translation_applied": False,
            }
        },
    }


def _with_current_value_serving(
    consumed: dict[str, dict[str, object]],
    *,
    anchor_artifact_id: int | None = None,
) -> dict[str, object]:
    provenance = _live_provenance()
    if anchor_artifact_id is not None:
        provenance["openmeteo_anchor_artifact_id"] = anchor_artifact_id
    fusion = provenance["bayes_precision_fusion"]
    assert isinstance(fusion, dict)
    fusion.update(
        {
            "used_models": list(consumed),
            "current_value_serving": consumed,
        }
    )
    return provenance


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
            json.dumps(_live_provenance()),
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
    endpoint: str = "single_runs",
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
            endpoint,
            "COVERED",
        ),
    )


def _insert_openmeteo_anchor_artifact(
    conn: sqlite3.Connection,
    tmp_path,
    *,
    source_cycle_time: datetime,
    city: str = "Shanghai",
    target_date: str = "2026-06-07",
    metric: str = "high",
) -> int:
    payload = tmp_path / (
        f"openmeteo-{city}-{target_date}-{metric}-"
        f"{source_cycle_time.strftime('%H%M')}.json"
    )
    payload_bytes = json.dumps(
        {
            "city": city,
            "hourly": {
                "time": [f"{target_date}T00:00", f"{target_date}T12:00"],
                "temperature_2m": [22.0, 28.0],
            },
        },
        sort_keys=True,
    ).encode()
    payload.write_bytes(payload_bytes)
    available_at = source_cycle_time + timedelta(minutes=5)
    conn.execute(
        """
        INSERT INTO raw_forecast_artifacts (
            source_id, product_id, data_version, source_cycle_time,
            source_available_at, captured_at, artifact_path, sha256,
            byte_size, artifact_metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            OPENMETEO_ANCHOR_SOURCE_ID,
            OPENMETEO_ANCHOR_PRODUCT_ID,
            f"openmeteo_ecmwf_ifs9_anchor_localday_{metric}",
            source_cycle_time.isoformat(),
            available_at.isoformat(),
            available_at.isoformat(),
            str(payload),
            hashlib.sha256(payload_bytes).hexdigest(),
            len(payload_bytes),
            json.dumps(
                {
                    "city": city,
                    "target_date": target_date,
                    "metric": metric,
                    "openmeteo_payload_json": str(payload),
                }
            ),
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


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
    assert result.bundle.posterior_identity_hash == (
        f"identity-{_dt(3, 5).isoformat()}-{_dt(3).isoformat()}"
    )
    assert result.bundle.dependency_hash == "dependency-hash"
    assert result.bundle.posterior_config_hash == "config-hash"


def test_replacement_bundle_reader_binds_to_readiness_posterior_not_latest_scope_row() -> None:
    conn = _conn()
    certified_posterior_id = _insert_posterior(conn, computed_at=_dt(3, 5))
    newer_posterior_id = _insert_posterior(conn, computed_at=_dt(3, 20))
    conn.execute(
        """
        UPDATE forecast_posteriors
           SET posterior_identity_hash = ?, dependency_hash = ?, posterior_config_hash = ?
         WHERE posterior_id = ?
        """,
        (
            "certified-identity",
            "certified-dependency",
            "certified-config",
            certified_posterior_id,
        ),
    )
    conn.execute(
        """
        UPDATE forecast_posteriors
           SET posterior_identity_hash = ?, dependency_hash = ?, posterior_config_hash = ?
         WHERE posterior_id = ?
        """,
        ("latest-identity", "latest-dependency", "latest-config", newer_posterior_id),
    )

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
    assert result.bundle.posterior_identity_hash == "certified-identity"
    assert result.bundle.dependency_hash == "certified-dependency"
    assert result.bundle.posterior_config_hash == "certified-config"


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


def test_raw_hwm_uses_exact_anchor_artifact_not_model_serving_clock(tmp_path) -> None:
    """The anchor artifact may be newer than the raw model row it accompanies."""
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_available_at=_dt(4, 5),
        computed_at=_dt(4, 10),
    )
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )
        raw_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        consumed[model] = {
            "raw_model_forecast_id": raw_id,
            "served_cycle": _dt(3).isoformat(),
            "captured_at": _dt(3, 5).isoformat(),
            "served_via": "single_runs",
        }
    anchor_artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(4),
    )
    provenance = _with_current_value_serving(
        consumed,
        anchor_artifact_id=anchor_artifact_id,
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), posterior_id),
    )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(5),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"


def test_raw_hwm_fails_closed_when_available_anchor_has_no_consumed_id(
    tmp_path,
) -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_available_at=_dt(4, 5),
        computed_at=_dt(4, 10),
    )
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(3).isoformat(),
            "captured_at": _dt(3, 5).isoformat(),
            "served_via": "single_runs",
        }
    _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(4),
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(_with_current_value_serving(consumed)), posterior_id),
    )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(5),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is False
    assert "openmeteo_anchor_artifact_provenance_unverifiable" in result.reason_code


def test_raw_hwm_blocks_newer_anchor_than_exact_consumed_artifact(tmp_path) -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_available_at=_dt(3, 5),
        computed_at=_dt(3, 10),
    )
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(3).isoformat(),
            "captured_at": _dt(3, 5).isoformat(),
            "served_via": "single_runs",
        }
    consumed_artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(3),
    )
    _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(4),
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (
            json.dumps(
                _with_current_value_serving(
                    consumed,
                    anchor_artifact_id=consumed_artifact_id,
                )
            ),
            posterior_id,
        ),
    )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(5),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is False
    assert "source_cycle_time_raw_forecast_artifacts_lag" in result.reason_code
    assert "consumed_anchor_cycle=2026-06-06T03:00:00+00:00" in result.reason_code


def test_raw_hwm_lookup_binds_exact_same_cycle_materialization(tmp_path) -> None:
    conn = _conn()
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(3).isoformat(),
            "captured_at": _dt(3, 5).isoformat(),
            "served_via": "single_runs",
        }
    older_artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(3),
    )
    older_id = _insert_posterior(
        conn,
        source_available_at=_dt(3, 5),
        computed_at=_dt(3, 10),
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (
            json.dumps(
                _with_current_value_serving(
                    consumed,
                    anchor_artifact_id=older_artifact_id,
                )
            ),
            older_id,
        ),
    )
    newer_artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(4),
    )
    newer_id = _insert_posterior(
        conn,
        source_available_at=_dt(4, 5),
        computed_at=_dt(4, 10),
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (
            json.dumps(
                _with_current_value_serving(
                    consumed,
                    anchor_artifact_id=newer_artifact_id,
                )
            ),
            newer_id,
        ),
    )

    reason = replacement_live_input_lag_reason(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(5),
        posterior_source_cycle_time=_dt(0),
        posterior_computed_at=_dt(3, 10),
    )

    assert reason is not None
    assert "source_cycle_time_raw_forecast_artifacts_lag" in reason
    assert "consumed_anchor_cycle=2026-06-06T03:00:00+00:00" in reason


@pytest.mark.parametrize(
    ("fault", "expected_basis"),
    (
        ("scope", "openmeteo_anchor_artifact_scope_mismatch"),
        ("future", "openmeteo_anchor_artifact_causality_mismatch"),
        ("hash", "openmeteo_anchor_artifact_payload_identity_mismatch"),
        ("metadata", "openmeteo_anchor_artifact_metadata_unverifiable"),
        ("coverage", "openmeteo_anchor_artifact_scope_mismatch"),
    ),
)
def test_exact_anchor_artifact_validation_fails_closed(
    tmp_path,
    fault: str,
    expected_basis: str,
) -> None:
    conn = _conn()
    artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(3),
    )
    row = conn.execute(
        """
        SELECT artifact_path, artifact_metadata_json
        FROM raw_forecast_artifacts
        WHERE artifact_id = ?
        """,
        (artifact_id,),
    ).fetchone()
    if fault == "scope":
        metadata = json.loads(row["artifact_metadata_json"])
        metadata["city"] = "Seoul"
        conn.execute(
            "UPDATE raw_forecast_artifacts SET artifact_metadata_json = ? WHERE artifact_id = ?",
            (json.dumps(metadata), artifact_id),
        )
    elif fault == "future":
        conn.execute(
            "UPDATE raw_forecast_artifacts SET source_available_at = ? WHERE artifact_id = ?",
            (_dt(6).isoformat(), artifact_id),
        )
    elif fault == "hash":
        conn.execute(
            "UPDATE raw_forecast_artifacts SET sha256 = ? WHERE artifact_id = ?",
            ("f" * 64, artifact_id),
        )
    elif fault == "metadata":
        conn.execute(
            "UPDATE raw_forecast_artifacts SET artifact_metadata_json = 'not-json' WHERE artifact_id = ?",
            (artifact_id,),
        )
    else:
        payload = {
            "hourly": {
                "time": ["2026-06-08T00:00"],
                "temperature_2m": [22.0],
            }
        }
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        path = row["artifact_path"]
        with open(path, "wb") as handle:
            handle.write(payload_bytes)
        conn.execute(
            "UPDATE raw_forecast_artifacts SET sha256 = ?, byte_size = ? WHERE artifact_id = ?",
            (hashlib.sha256(payload_bytes).hexdigest(), len(payload_bytes), artifact_id),
        )

    reason, cycle = _exact_consumed_anchor_artifact_cycle(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(5),
        provenance={"openmeteo_anchor_artifact_id": artifact_id},
    )

    assert cycle is None
    assert reason is not None
    assert expected_basis in reason


def test_raw_hwm_accepts_exact_authoritative_previous_run_substitution() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    consumed: dict[str, dict[str, object]] = {}
    for model, endpoint in (
        ("ecmwf_ifs", "single_runs"),
        ("gfs", "previous_runs"),
    ):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
            endpoint=endpoint,
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(3).isoformat(),
            "captured_at": _dt(3, 5).isoformat(),
            "served_via": endpoint,
        }
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(_with_current_value_serving(consumed)), posterior_id),
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


def test_raw_hwm_blocks_when_exact_consumed_model_is_superseded() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(2),
            captured_at=_dt(2, 5),
            source_available_at=_dt(2, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(2).isoformat(),
            "captured_at": _dt(2, 5).isoformat(),
            "served_via": "single_runs",
        }
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (
            json.dumps(_with_current_value_serving(consumed)),
            posterior_id,
        ),
    )
    _insert_raw_model_forecast(
        conn,
        model="gfs",
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
    assert "basis=used_raw_model_forecasts_superseded" in result.reason_code
    assert "model=gfs" in result.reason_code


def test_raw_hwm_fails_closed_on_unverifiable_current_value_provenance() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    _insert_raw_model_forecast(
        conn,
        model="gfs",
        source_cycle_time=_dt(0),
        captured_at=_dt(0, 5),
        source_available_at=_dt(0, 5),
    )
    provenance = _with_current_value_serving(
        {
            "gfs": {
                "raw_model_forecast_id": int(
                    conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                ),
                "served_via": "single_runs",
            }
        }
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), posterior_id),
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
    assert "current_value_serving_provenance_unverifiable" in result.reason_code


def test_raw_hwm_reuses_bound_posterior_provenance(monkeypatch) -> None:
    import src.data.replacement_forecast_bundle_reader as reader

    conn = _conn()
    posterior_id = _insert_posterior(conn)
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(0),
            captured_at=_dt(0, 5),
            source_available_at=_dt(0, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(0).isoformat(),
            "captured_at": _dt(0, 5).isoformat(),
            "served_via": "single_runs",
        }
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(_with_current_value_serving(consumed)), posterior_id),
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
    posterior_reads = [
        statement
        for statement in traced
        if statement.lstrip().upper().startswith("SELECT")
        and "FROM FORECAST_POSTERIORS" in statement.upper()
    ]
    assert len(posterior_reads) == 1
