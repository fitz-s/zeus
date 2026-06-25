# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast live-support tables from contaminating raw ensemble snapshots.
# Reuse: Run before changing replacement forecast artifact/posterior/live-support schema.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + Bayes fusion integration.
"""Replacement forecast live-support schema tests."""

from __future__ import annotations

import sqlite3

import pytest

from src.state.schema.v2_schema import apply_canonical_schema
from scripts.init_replacement_forecast_live_schema import (
    REPLACEMENT_LIVE_TABLES,
    initialize_replacement_forecast_live_schema,
)


REPLACEMENT_TABLES = {
    "raw_forecast_artifacts",
    "deterministic_forecast_anchors",
    "forecast_posteriors",
    "replacement_shadow_decisions",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_replacement_live_support_tables_are_forecast_class_only() -> None:
    forecast_conn = sqlite3.connect(":memory:")
    apply_canonical_schema(forecast_conn, forecast_tables=True)

    assert REPLACEMENT_TABLES <= _tables(forecast_conn)
    assert {
        "source_id",
        "product_id",
        "data_version",
        "source_cycle_time",
        "source_available_at",
        "captured_at",
        "sha256",
        "byte_size",
        "training_allowed",
    } <= _columns(forecast_conn, "raw_forecast_artifacts")
    assert {"anchor_value_c", "native_grid", "delivery_grid_resolution", "interpolation_method"} <= _columns(
        forecast_conn,
        "deterministic_forecast_anchors",
    )
    assert {"q_json", "q_lcb_json", "openmeteo_anchor_id"} <= _columns(
        forecast_conn,
        "forecast_posteriors",
    )
    assert "runtime_layer" in _columns(forecast_conn, "forecast_posteriors")
    assert "trade_authority_status" not in _columns(forecast_conn, "forecast_posteriors")
    assert {"market_snapshot_id", "allowed_direction", "allowed_q_lcb", "allowed_kelly_fraction", "veto_reason"} <= _columns(
        forecast_conn,
        "replacement_shadow_decisions",
    )

    world_conn = sqlite3.connect(":memory:")
    apply_canonical_schema(world_conn, forecast_tables=False)
    assert REPLACEMENT_TABLES.isdisjoint(_tables(world_conn))


def test_targeted_replacement_live_schema_initializer_dry_run_rolls_back(tmp_path) -> None:
    db_path = tmp_path / "zeus-forecasts.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE ensemble_snapshots (id INTEGER PRIMARY KEY)")
        conn.commit()

    report = initialize_replacement_forecast_live_schema(db_path, commit=False)

    assert report["status"] == "READY"
    assert set(report["created_tables"]) == set(REPLACEMENT_LIVE_TABLES)
    with sqlite3.connect(db_path) as conn:
        assert REPLACEMENT_TABLES.isdisjoint(_tables(conn))


def test_targeted_replacement_live_schema_initializer_commit_creates_only_live_support_tables(tmp_path) -> None:
    db_path = tmp_path / "zeus-forecasts.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE ensemble_snapshots (id INTEGER PRIMARY KEY)")
        conn.commit()

    report = initialize_replacement_forecast_live_schema(db_path, commit=True)

    assert report["status"] == "READY"
    with sqlite3.connect(db_path) as conn:
        tables = _tables(conn)
    assert REPLACEMENT_TABLES <= tables
    assert "settlement_outcomes" not in tables
    assert "venue_commands" not in tables


def test_legacy_forecast_posteriors_live_status_migrates_to_runtime_layer() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            data_version TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            source_available_at TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            q_json TEXT NOT NULL,
            posterior_method TEXT NOT NULL,
            bin_topology_hash TEXT,
            posterior_identity_hash TEXT,
            trade_authority_status TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, posterior_method, bin_topology_hash,
            posterior_identity_hash, trade_authority_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "replacement",
                "replacement_v1",
                "replacement_high_v1",
                "Shanghai",
                "2026-06-06",
                "high",
                "2026-06-06T00:00:00+00:00",
                "2026-06-06T02:00:00+00:00",
                "2026-06-06T02:05:00+00:00",
                '{"warm": 1.0}',
                "replacement",
                "topology-1",
                "identity-live",
                "LIVE_AUTHORITY",
            ),
            (
                "replacement",
                "replacement_v1",
                "replacement_high_v1",
                "Shanghai",
                "2026-06-06",
                "high",
                "2026-06-06T06:00:00+00:00",
                "2026-06-06T08:00:00+00:00",
                "2026-06-06T08:05:00+00:00",
                '{"warm": 0.8}',
                "replacement",
                "topology-1",
                "identity-diagnostic",
                "DIAGNOSTIC_ONLY",
            ),
        ],
    )

    apply_canonical_schema(conn, forecast_tables=True)

    assert "runtime_layer" in _columns(conn, "forecast_posteriors")
    assert "trade_authority_status" not in _columns(conn, "forecast_posteriors")
    rows = conn.execute(
        "SELECT posterior_id, runtime_layer FROM forecast_posteriors ORDER BY posterior_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [(1, "live")]


def test_legacy_raw_model_forecasts_diagnostic_status_column_is_removed() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            metric TEXT NOT NULL CHECK (metric IN ('high', 'low')),
            source_cycle_time TEXT NOT NULL,
            source_available_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            lead_days INTEGER NOT NULL CHECK (lead_days >= 0),
            forecast_value_c REAL NOT NULL,
            endpoint TEXT NOT NULL CHECK (endpoint IN ('single_runs', 'previous_runs')),
            trade_authority_status TEXT NOT NULL DEFAULT 'DIAGNOSTIC_ONLY'
                CHECK (trade_authority_status IN ('DIAGNOSTIC_ONLY')),
            training_allowed INTEGER NOT NULL DEFAULT 0
                CHECK (training_allowed = 0),
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source_id TEXT,
            source_family TEXT,
            product_id TEXT,
            provider TEXT,
            model_name TEXT,
            request_params_json TEXT NOT NULL DEFAULT '{}',
            request_url_hash TEXT,
            raw_sha256 TEXT,
            latitude_requested REAL,
            longitude_requested REAL,
            timezone_requested TEXT,
            cell_selection TEXT,
            elevation_param TEXT,
            downscaling_policy TEXT,
            endpoint_mode TEXT,
            model_domain_hash TEXT,
            coverage_status TEXT,
            artifact_id INTEGER,
            UNIQUE(model, city, target_date, metric, source_cycle_time, endpoint)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time,
            source_available_at, captured_at, lead_days, forecast_value_c,
            endpoint, training_allowed, source_id, source_family, product_id,
            provider, model_name, request_params_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ecmwf_ifs",
            "Shanghai",
            "2026-06-26",
            "high",
            "2026-06-24T12:00:00Z",
            "2026-06-24T18:00:00Z",
            "2026-06-25T03:00:00Z",
            2,
            30.0,
            "single_runs",
            0,
            "openmeteo:ecmwf_ifs",
            "openmeteo",
            "ecmwf_ifs:single_runs",
            "openmeteo",
            "ecmwf_ifs",
            "{}",
        ),
    )

    apply_canonical_schema(conn, forecast_tables=True)

    assert "trade_authority_status" not in _columns(conn, "raw_model_forecasts")
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    row = conn.execute(
        """
        SELECT model, city, target_date, metric, forecast_value_c, training_allowed
        FROM raw_model_forecasts
        """
    ).fetchone()
    assert tuple(row) == ("ecmwf_ifs", "Shanghai", "2026-06-26", "high", 30.0, 0)


def test_replacement_raw_artifacts_are_not_training_authority() -> None:
    conn = sqlite3.connect(":memory:")
    apply_canonical_schema(conn, forecast_tables=True)

    conn.execute(
        """
        INSERT INTO raw_forecast_artifacts (
            source_id, product_id, data_version, source_cycle_time,
            source_available_at, captured_at, artifact_path, sha256, byte_size
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ecmwf_aifs_ens",
            "ecmwf_aifs_ens_sampled_2t_6h_v1",
            "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            "2026-06-06T00:00:00+00:00",
            "2026-06-06T01:00:00+00:00",
            "2026-06-06T01:05:00+00:00",
            "/tmp/aifs.grib2",
            "abc123",
            123,
        ),
    )

    with pytest.raises(sqlite3.OperationalError):
        conn.execute(
            """
            INSERT INTO raw_forecast_artifacts (
                source_id, product_id, data_version, source_cycle_time,
                source_available_at, captured_at, artifact_path, sha256, byte_size,
                trade_authority_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ecmwf_aifs_ens",
                "ecmwf_aifs_ens_sampled_2t_6h_v1",
                "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
                "2026-06-06T00:00:00+00:00",
                "2026-06-06T01:00:00+00:00",
                "2026-06-06T01:05:00+00:00",
                "/tmp/aifs.grib2",
                "abc124",
                123,
                "ENTRY_PRIMARY",
            ),
        )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO raw_forecast_artifacts (
                source_id, product_id, data_version, source_cycle_time,
                source_available_at, captured_at, artifact_path, sha256, byte_size,
                training_allowed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ecmwf_aifs_ens",
                "ecmwf_aifs_ens_sampled_2t_6h_v1",
                "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
                "2026-06-06T00:00:00+00:00",
                "2026-06-06T01:00:00+00:00",
                "2026-06-06T01:05:00+00:00",
                "/tmp/aifs.grib2",
                "abc125",
                123,
                1,
            ),
        )


def test_replacement_posteriors_and_decisions_cannot_increase_authority_shape() -> None:
    conn = sqlite3.connect(":memory:")
    apply_canonical_schema(conn, forecast_tables=True)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, posterior_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
            "Shanghai",
            "2026-06-06",
            "high",
            "2026-06-06T00:00:00+00:00",
            "2026-06-06T02:00:00+00:00",
            "2026-06-06T02:05:00+00:00",
            '{"warm": 1.0}',
            "openmeteo_ifs9_aifs_sampled_2t_soft_anchor",
        ),
    )
    posterior_id = conn.execute("SELECT posterior_id FROM forecast_posteriors").fetchone()[0]
    assert conn.execute(
        "SELECT runtime_layer FROM forecast_posteriors WHERE posterior_id = ?",
        (posterior_id,),
    ).fetchone()[0] == "live"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, product_id, data_version, city, target_date,
                temperature_metric, source_cycle_time, source_available_at,
                computed_at, q_json, posterior_method, runtime_layer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
                "Shanghai",
                "2026-06-06",
                "high",
                "2026-06-06T06:00:00+00:00",
                "2026-06-06T08:00:00+00:00",
                "2026-06-06T08:05:00+00:00",
                '{"warm": 1.0}',
                "openmeteo_ifs9_aifs_sampled_2t_soft_anchor",
                "experiment",
            ),
        )

    conn.execute(
        """
        INSERT INTO replacement_shadow_decisions (
            posterior_id, market_snapshot_id, condition_id, token_id,
            decision_time, baseline_direction, candidate_direction,
            allowed_direction, baseline_q_lcb, candidate_q_lcb, allowed_q_lcb,
            baseline_kelly_fraction, candidate_kelly_fraction,
            allowed_kelly_fraction, veto, veto_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            posterior_id,
            "snap-1",
            "cond-1",
            "token-yes",
            "2026-06-06T02:10:00+00:00",
            "buy_yes:warm",
            "buy_yes:hot",
            "buy_yes:warm",
            0.62,
            0.55,
            0.55,
            0.04,
            0.01,
            0.01,
            1,
            "SOFT_ANCHOR_LOWER_Q_LCB",
        ),
    )

    with pytest.raises(sqlite3.OperationalError):
        conn.execute(
            """
            INSERT INTO replacement_shadow_decisions (
                posterior_id, market_snapshot_id, condition_id, token_id,
                decision_time, baseline_direction, candidate_direction,
                allowed_direction, baseline_q_lcb, candidate_q_lcb, allowed_q_lcb,
                baseline_kelly_fraction, candidate_kelly_fraction,
                allowed_kelly_fraction, veto, trade_authority_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                posterior_id,
                "snap-2",
                "cond-1",
                "token-yes",
                "2026-06-06T02:11:00+00:00",
                "buy_yes:warm",
                "buy_yes:hot",
                "buy_yes:warm",
                0.62,
                0.55,
                0.55,
                0.04,
                0.01,
                0.01,
                1,
                "ENTRY_PRIMARY",
            ),
        )
