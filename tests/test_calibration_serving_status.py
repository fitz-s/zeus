# Created: 2026-05-09
# Last reused/audited: 2026-05-09
# Authority basis: S3 calibration serving status surface packet; TASK.md safe implementation queue.
"""Relationship tests for derived calibration-serving visibility."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema

UTC = timezone.utc
BUCKET_CLUSTER = "London"
BUCKET_SEASON = "MAM"
BUCKET_SOURCE_ID = "ecmwf_open_data"
BUCKET_TRACK = "mx2t6_high_full_horizon"
BUCKET_CITY = "London"
BUCKET_DATA_VERSION = ECMWF_OPENDATA_HIGH_DATA_VERSION


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _insert_producer_readiness(
    conn: sqlite3.Connection,
    *,
    status: str = "LIVE_ELIGIBLE",
    expires_at: datetime | None = None,
    suffix: str | None = None,
    computed_at: datetime | None = None,
) -> None:
    if expires_at is None and status == "LIVE_ELIGIBLE":
        expires_at = _utc(2026, 5, 3, 12)
    row_suffix = suffix or status
    if computed_at is None:
        computed_at = _utc(2026, 5, 3, 9)
    conn.execute(
        """
        INSERT INTO readiness_state (
            readiness_id, scope_key, scope_type, city_id, city, city_timezone,
            target_local_date, metric, temperature_metric, physical_quantity,
            observation_field, data_version, source_id, track, source_run_id,
            market_family, event_id, condition_id, token_ids_json,
            strategy_key, status, reason_codes_json, computed_at, expires_at,
            dependency_json, provenance_json
        ) VALUES (
            :readiness_id, :scope_key, 'city_metric', 'LONDON', :city, 'Europe/London',
            '2026-05-08', NULL, 'high', 'mx2t6_local_calendar_day_max',
            'high_temp', :data_version, :source_id, :track, 'source-run-1',
            NULL, NULL, NULL, '[]',
            :strategy_key, :status, :reason_codes_json, :computed_at, :expires_at,
            '{}', '{}'
        )
        """,
        {
            "readiness_id": f"producer-readiness-{row_suffix}",
            "scope_key": f"producer|{row_suffix}",
            "city": BUCKET_CITY,
            "data_version": BUCKET_DATA_VERSION,
            "source_id": BUCKET_SOURCE_ID,
            "track": BUCKET_TRACK,
            "strategy_key": PRODUCER_READINESS_STRATEGY_KEY,
            "status": status,
            "reason_codes_json": json.dumps(["PRODUCER_COVERAGE_READY"] if status == "LIVE_ELIGIBLE" else ["SOURCE_RUN_COVERAGE_BLOCKED"]),
            "computed_at": computed_at.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )


def _insert_verified_pair(conn: sqlite3.Connection, *, authority: str = "VERIFIED") -> None:
    conn.execute(
        """
        INSERT INTO calibration_pairs_v2 (
            city, target_date, temperature_metric, observation_field, range_label,
            p_raw, outcome, lead_days, season, cluster, forecast_available_at,
            decision_group_id, authority, bin_source, data_version,
            training_allowed, causality_status, cycle, source_id, horizon_profile
        ) VALUES (
            :city, '2026-05-08', 'high', 'high_temp', '60-61F',
            0.42, 1, 5.0, :season, :cluster, '2026-05-03T08:10:00+00:00',
            'decision-group-1', :authority, 'canonical_v1', :data_version,
            1, 'OK', '00', :source_id, 'full'
        )
        """,
        {
            "city": BUCKET_CITY,
            "season": BUCKET_SEASON,
            "cluster": BUCKET_CLUSTER,
            "authority": authority,
            "data_version": BUCKET_DATA_VERSION,
            "source_id": BUCKET_SOURCE_ID,
        },
    )


def _insert_active_model(conn: sqlite3.Connection, *, authority: str = "VERIFIED") -> None:
    conn.execute(
        """
        INSERT INTO platt_models_v2 (
            model_key, temperature_metric, cluster, season, data_version, input_space,
            param_A, param_B, param_C, bootstrap_params_json, n_samples,
            brier_insample, fitted_at, is_active, authority, bucket_key,
            cycle, source_id, horizon_profile
        ) VALUES (
            :model_key, 'high', :cluster, :season, :data_version, 'width_normalized_density',
            1.0, 0.1, -0.2, '[[1.0, 0.1, -0.2]]', 200,
            0.18, '2026-05-03T00:00:00+00:00', 1, :authority, :bucket_key,
            '00', :source_id, 'full'
        )
        """,
        {
            "model_key": f"high:{BUCKET_CLUSTER}:{BUCKET_SEASON}:{BUCKET_DATA_VERSION}:00:{BUCKET_SOURCE_ID}:full:width_normalized_density",
            "cluster": BUCKET_CLUSTER,
            "season": BUCKET_SEASON,
            "data_version": BUCKET_DATA_VERSION,
            "authority": authority,
            "bucket_key": f"high:{BUCKET_CLUSTER}:{BUCKET_SEASON}:{BUCKET_DATA_VERSION}:{BUCKET_SOURCE_ID}",
            "source_id": BUCKET_SOURCE_ID,
        },
    )


def _first_bucket(report: dict) -> dict:
    assert report["authority"] == "derived_operator_visibility"
    assert report["buckets"]
    return report["buckets"][0]


def test_producer_ready_without_calibration_is_not_calibration_ready() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = _conn()
    _insert_producer_readiness(conn, status="LIVE_ELIGIBLE")

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 10))
    bucket = _first_bucket(report)

    assert bucket["forecast_ready"] is True
    assert bucket["calibration_ready"] is False
    assert bucket["trade_ready"] is False
    assert "CALIBRATION_PAIRS_ABSENT" in bucket["calibration_blockers"]
    assert "PLATT_MODEL_ABSENT" in bucket["calibration_blockers"]


def test_unverified_calibration_evidence_does_not_satisfy_calibration_ready() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = _conn()
    _insert_producer_readiness(conn, status="LIVE_ELIGIBLE")
    _insert_verified_pair(conn, authority="UNVERIFIED")
    _insert_active_model(conn, authority="UNVERIFIED")

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 10))
    bucket = _first_bucket(report)

    assert bucket["forecast_ready"] is True
    assert bucket["calibration_ready"] is False
    assert "CALIBRATION_PAIRS_ABSENT" in bucket["calibration_blockers"]
    assert "PLATT_MODEL_ABSENT" in bucket["calibration_blockers"]


def test_calibration_ready_does_not_imply_forecast_ready() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = _conn()
    _insert_producer_readiness(conn, status="BLOCKED")
    _insert_verified_pair(conn)
    _insert_active_model(conn)

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 10))
    bucket = _first_bucket(report)

    assert bucket["forecast_ready"] is False
    assert bucket["calibration_ready"] is True
    assert bucket["trade_ready"] is False
    assert "FORECAST_NOT_LIVE_ELIGIBLE" in bucket["forecast_blockers"]


def test_forecast_and_calibration_ready_yields_trade_ready_visibility() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = _conn()
    _insert_producer_readiness(conn, status="LIVE_ELIGIBLE")
    _insert_verified_pair(conn)
    _insert_active_model(conn)

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 10))
    bucket = _first_bucket(report)

    assert bucket["forecast_ready"] is True
    assert bucket["calibration_ready"] is True
    assert bucket["trade_ready"] is True
    assert bucket["authority"] == "derived_operator_visibility"


def test_expired_producer_readiness_keeps_forecast_ready_false() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = _conn()
    _insert_producer_readiness(conn, status="LIVE_ELIGIBLE", expires_at=_utc(2026, 5, 3, 9))
    _insert_verified_pair(conn)
    _insert_active_model(conn)

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 10))
    bucket = _first_bucket(report)

    assert bucket["forecast_ready"] is False
    assert bucket["calibration_ready"] is True
    assert "PRODUCER_READINESS_EXPIRED" in bucket["forecast_blockers"]


def test_latest_producer_readiness_telemetry_uses_newest_row() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = _conn()
    _insert_producer_readiness(
        conn,
        status="BLOCKED",
        suffix="old-blocked",
        computed_at=_utc(2026, 5, 3, 8),
    )
    _insert_producer_readiness(
        conn,
        status="LIVE_ELIGIBLE",
        suffix="new-live",
        computed_at=_utc(2026, 5, 3, 9),
    )

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 10))
    bucket = _first_bucket(report)

    assert bucket["forecast_ready"] is True
    assert bucket["producer"]["latest_readiness_id"] == "producer-readiness-new-live"
    assert bucket["producer"]["latest_status"] == "LIVE_ELIGIBLE"


def test_missing_tables_return_query_error_without_mutation() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 10))

    assert report["status"] == "query_error"
    assert report["authority"] == "derived_operator_visibility"
    assert {error["source"] for error in report["source_errors"]} == {
        "readiness_state",
        "platt_models_v2",
        "calibration_pairs_v2",
    }


def test_empty_tables_return_certified_empty_visibility() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = _conn()

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 10))

    assert report["status"] == "certified_empty"
    assert report["authority"] == "derived_operator_visibility"
    assert report["buckets"] == []
    assert report["source_errors"] == []
