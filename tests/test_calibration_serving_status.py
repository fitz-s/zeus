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
TIGGE_HIGH_DATA_VERSION = "tigge_mx2t6_local_calendar_day_max_v1"


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
    source_id: str = BUCKET_SOURCE_ID,
    data_version: str = BUCKET_DATA_VERSION,
    track: str = BUCKET_TRACK,
    source_cycle_hour: int = 0,
    write_source_run: bool = True,
) -> None:
    if expires_at is None and status == "LIVE_ELIGIBLE":
        expires_at = _utc(2026, 5, 3, 12)
    row_suffix = suffix or status
    if computed_at is None:
        computed_at = _utc(2026, 5, 3, 9)
    source_run_id = f"source-run-{row_suffix}"
    if write_source_run:
        conn.execute(
            """
            INSERT OR IGNORE INTO source_run (
                source_run_id, source_id, track, release_calendar_key,
                ingest_mode, origin_mode, source_cycle_time,
                city_id, city_timezone, target_local_date, temperature_metric,
                physical_quantity, observation_field, data_version,
                completeness_status, status
            ) VALUES (
                :source_run_id, :source_id, :track, :release_calendar_key,
                'SCHEDULED_LIVE', 'SCHEDULED_LIVE', :source_cycle_time,
                'LONDON', 'Europe/London', '2026-05-08', 'high',
                'mx2t6_local_calendar_day_max', 'high_temp', :data_version,
                'COMPLETE', 'SUCCESS'
            )
            """,
            {
                "source_run_id": source_run_id,
                "source_id": source_id,
                "track": track,
                "release_calendar_key": f"{source_id}:{track}",
                "source_cycle_time": _utc(2026, 5, 3, source_cycle_hour).isoformat(),
                "data_version": data_version,
            },
        )
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
            'high_temp', :data_version, :source_id, :track, :source_run_id,
            NULL, NULL, NULL, '[]',
            :strategy_key, :status, :reason_codes_json, :computed_at, :expires_at,
            '{}', '{}'
        )
        """,
        {
            "readiness_id": f"producer-readiness-{row_suffix}",
            "scope_key": f"producer|{row_suffix}",
            "city": BUCKET_CITY,
            "data_version": data_version,
            "source_id": source_id,
            "track": track,
            "source_run_id": source_run_id,
            "strategy_key": PRODUCER_READINESS_STRATEGY_KEY,
            "status": status,
            "reason_codes_json": json.dumps(["PRODUCER_COVERAGE_READY"] if status == "LIVE_ELIGIBLE" else ["SOURCE_RUN_COVERAGE_BLOCKED"]),
            "computed_at": computed_at.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )


def _insert_verified_pair(
    conn: sqlite3.Connection,
    *,
    authority: str = "VERIFIED",
    source_id: str = BUCKET_SOURCE_ID,
    data_version: str = BUCKET_DATA_VERSION,
    cycle: str = "00",
    horizon_profile: str = "full",
) -> None:
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
            1, 'OK', :cycle, :source_id, :horizon_profile
        )
        """,
        {
            "city": BUCKET_CITY,
            "season": BUCKET_SEASON,
            "cluster": BUCKET_CLUSTER,
            "authority": authority,
            "data_version": data_version,
            "cycle": cycle,
            "source_id": source_id,
            "horizon_profile": horizon_profile,
        },
    )


def _insert_active_model(
    conn: sqlite3.Connection,
    *,
    authority: str = "VERIFIED",
    source_id: str = BUCKET_SOURCE_ID,
    data_version: str = BUCKET_DATA_VERSION,
    cycle: str = "00",
    horizon_profile: str = "full",
) -> None:
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
            :cycle, :source_id, :horizon_profile
        )
        """,
        {
            "model_key": f"high:{BUCKET_CLUSTER}:{BUCKET_SEASON}:{data_version}:{cycle}:{source_id}:{horizon_profile}:width_normalized_density",
            "cluster": BUCKET_CLUSTER,
            "season": BUCKET_SEASON,
            "data_version": data_version,
            "authority": authority,
            "bucket_key": f"high:{BUCKET_CLUSTER}:{BUCKET_SEASON}:{data_version}:{cycle}:{source_id}:{horizon_profile}",
            "cycle": cycle,
            "source_id": source_id,
            "horizon_profile": horizon_profile,
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


def test_raw_tigge_forecast_source_uses_calibration_source_bucket() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = _conn()
    _insert_producer_readiness(
        conn,
        status="LIVE_ELIGIBLE",
        source_id="tigge",
        data_version=TIGGE_HIGH_DATA_VERSION,
        source_cycle_hour=0,
    )
    _insert_verified_pair(conn, source_id="tigge_mars", data_version=TIGGE_HIGH_DATA_VERSION)
    _insert_active_model(conn, source_id="tigge_mars", data_version=TIGGE_HIGH_DATA_VERSION)

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 10))
    bucket = _first_bucket(report)

    assert report["bucket_count"] == 1
    assert bucket["forecast_ready"] is True
    assert bucket["calibration_ready"] is True
    assert bucket["trade_ready"] is True
    assert bucket["serving_bucket"]["forecast_source_id"] == "tigge"
    assert bucket["serving_bucket"]["source_id"] == "tigge_mars"


def test_calibration_ready_requires_matching_cycle_and_horizon_profile() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = _conn()
    _insert_producer_readiness(
        conn,
        status="LIVE_ELIGIBLE",
        suffix="producer-12z",
        source_cycle_hour=12,
        expires_at=_utc(2026, 5, 3, 15),
    )
    _insert_verified_pair(conn, cycle="00", horizon_profile="full")
    _insert_active_model(conn, cycle="00", horizon_profile="full")
    _insert_active_model(conn, cycle="12", horizon_profile="short")

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 13))
    producer_bucket = next(bucket for bucket in report["buckets"] if bucket["producer"]["readiness_count"] > 0)

    assert producer_bucket["serving_bucket"]["cycle"] == "12"
    assert producer_bucket["serving_bucket"]["horizon_profile"] == "full"
    assert producer_bucket["forecast_ready"] is True
    assert producer_bucket["calibration_ready"] is False
    assert producer_bucket["trade_ready"] is False
    assert "CALIBRATION_PAIRS_ABSENT" in producer_bucket["calibration_blockers"]
    assert "PLATT_MODEL_ABSENT" in producer_bucket["calibration_blockers"]


def test_missing_source_run_evidence_does_not_infer_cycle_ready() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = _conn()
    _insert_producer_readiness(
        conn,
        status="LIVE_ELIGIBLE",
        suffix="legacy-no-source-run",
        write_source_run=False,
    )
    _insert_verified_pair(conn, cycle="00", horizon_profile="full")
    _insert_active_model(conn, cycle="00", horizon_profile="full")

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 10))
    producer_bucket = next(bucket for bucket in report["buckets"] if bucket["producer"]["readiness_count"] > 0)

    assert producer_bucket["forecast_ready"] is True
    assert producer_bucket["calibration_ready"] is False
    assert producer_bucket["serving_bucket"]["cycle"] == "unknown"
    assert "CALIBRATION_CYCLE_UNRESOLVED" in producer_bucket["calibration_blockers"]


def test_unmapped_forecast_source_id_blocks_calibration_ready() -> None:
    from src.observability.calibration_serving_status import build_calibration_serving_status

    conn = _conn()
    _insert_producer_readiness(
        conn,
        status="LIVE_ELIGIBLE",
        suffix="novel-source",
        source_id="novel_forecast_source",
    )

    report = build_calibration_serving_status(conn, now_utc=_utc(2026, 5, 3, 10))
    bucket = _first_bucket(report)

    assert bucket["forecast_ready"] is True
    assert bucket["calibration_ready"] is False
    assert bucket["serving_bucket"]["source_id"] == "novel_forecast_source"
    assert "CALIBRATION_SOURCE_UNMAPPED" in bucket["calibration_blockers"]


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
