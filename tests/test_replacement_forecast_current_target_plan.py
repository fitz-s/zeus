# Created: 2026-06-06
# Last reused/audited: 2026-06-07
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-07
# Purpose: Protect current-market replacement forecast download and materialization planning.
# Reuse: Run before changing current replacement target coverage or source-run matching.
# Authority basis: Replacement forecast coverage must bind to the live baseline source_run, not stale city/date rows.
"""Tests for current-market replacement forecast download planning."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.data.replacement_forecast_current_target_plan import (
    build_replacement_forecast_current_target_plan,
    replacement_forecast_download_plan_from_current_targets,
)


def _create_db(path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE market_events (
                event_id INTEGER PRIMARY KEY,
                market_slug TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                condition_id TEXT,
                token_id TEXT,
                range_label TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE forecast_posteriors (
                posterior_id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                data_version TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                dependency_source_run_ids_json TEXT,
                trade_authority_status TEXT NOT NULL,
                training_allowed INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE readiness_state (
                readiness_id TEXT PRIMARY KEY,
                strategy_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'SHADOW_ONLY',
                dependency_json TEXT NOT NULL DEFAULT '{}',
                provenance_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE source_run_coverage (
                coverage_id TEXT PRIMARY KEY,
                source_run_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                city TEXT NOT NULL,
                target_local_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                data_version TEXT NOT NULL,
                completeness_status TEXT NOT NULL,
                readiness_status TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE raw_forecast_artifacts (
                artifact_id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                data_version TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                product_metadata_json TEXT NOT NULL
            )
            """
        )
        for city in ("Madrid", "London", "Paris"):
            conn.execute(
                """
                INSERT INTO market_events (
                    market_slug, city, target_date, temperature_metric,
                    condition_id, token_id, range_label
                ) VALUES (?, ?, '2026-06-09', 'high', 'condition', ?, ?)
                """,
                (
                    f"highest-temperature-in-{city.lower()}-on-june-9-2026",
                    city,
                    f"token-{city}",
                    f"Will the highest temperature in {city} be 30°C on June 9?",
                ),
            )
            conn.execute(
                """
                INSERT INTO source_run_coverage (
                    coverage_id, source_run_id, source_id, city, target_local_date,
                    temperature_metric, data_version, completeness_status,
                    readiness_status, computed_at, recorded_at
                ) VALUES (?, ?, 'ecmwf_open_data', ?, '2026-06-09',
                    'high', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
                    'COMPLETE', 'LIVE_ELIGIBLE',
                    '2026-06-07T08:00:00+00:00',
                    '2026-06-07T08:00:00+00:00')
                """,
                (f"coverage-{city}", f"baseline-current-{city}", city),
            )
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, product_id, data_version, city, target_date,
                temperature_metric, dependency_source_run_ids_json,
                trade_authority_status, training_allowed
            ) VALUES (
                'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor',
                'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1',
                'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1',
                'Paris', '2026-06-09', 'high',
                '{"baseline_b0":"baseline-current-Paris"}',
                'SHADOW_VETO_ONLY', 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, product_id, data_version, city, target_date,
                temperature_metric, dependency_source_run_ids_json,
                trade_authority_status, training_allowed
            ) VALUES (
                'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor',
                'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1',
                'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1',
                'Madrid', '2026-06-09', 'high',
                '{"baseline_b0":"baseline-stale-Madrid"}',
                'SHADOW_VETO_ONLY', 0
            )
            """
        )
        conn.execute(
            """
                INSERT INTO readiness_state (
                    readiness_id, strategy_key, status, dependency_json, provenance_json
                ) VALUES (?, ?, 'SHADOW_ONLY', ?, ?)
            """,
            (
                "ready-paris",
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
                json.dumps({"dependencies": [{"role": "baseline_b0", "source_run_id": "baseline-current-Paris"}]}),
                json.dumps({"city": "Paris", "target_date": "2026-06-09", "temperature_metric": "high"}),
            ),
        )
        conn.execute(
            """
                INSERT INTO readiness_state (
                    readiness_id, strategy_key, status, dependency_json, provenance_json
                ) VALUES (?, ?, 'SHADOW_ONLY', ?, ?)
            """,
            (
                "ready-madrid-stale",
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
                json.dumps({"dependencies": [{"role": "baseline_b0", "source_run_id": "baseline-stale-Madrid"}]}),
                json.dumps({"city": "Madrid", "target_date": "2026-06-09", "temperature_metric": "high"}),
            ),
        )
        for source_id, product_id, data_version in (
            (
                "ecmwf_aifs_ens",
                "ecmwf_aifs_ens_sampled_2t_6h_v1",
                "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            ),
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "openmeteo_ecmwf_ifs9_anchor_localday_high",
            ),
        ):
            conn.execute(
                """
                INSERT INTO raw_forecast_artifacts (
                    source_id, product_id, data_version, artifact_path, product_metadata_json
                ) VALUES (?, ?, ?, '/tmp/artifact', ?)
                """,
                (
                    source_id,
                    product_id,
                    data_version,
                    json.dumps({"cities": ["London"], "target_dates": ["2026-06-09"]}),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def test_current_target_plan_classifies_covered_seedable_and_missing_manifest_targets(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)

    plan = build_replacement_forecast_current_target_plan(db)
    download_plan = replacement_forecast_download_plan_from_current_targets(plan)

    assert plan.status == "CURRENT_TARGETS_MISSING_REPLACEMENT_COVERAGE"
    assert plan.target_count == 3
    assert plan.covered_count == 1
    assert plan.can_seed_count == 1
    assert plan.missing_aifs_manifest_count == 1
    assert plan.missing_openmeteo_manifest_count == 1
    assert [row["city"] for row in download_plan["seedable_targets"]] == ["London"]
    assert [row["city"] for row in download_plan["aifs_download_targets"]] == ["Madrid"]
    assert [row["city"] for row in download_plan["openmeteo_download_targets"]] == ["Madrid"]


def test_current_target_plan_does_not_treat_blocked_replacement_readiness_as_covered(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE readiness_state SET status = 'BLOCKED' WHERE readiness_id = 'ready-paris'")
        for source_id, product_id, data_version in (
            (
                "ecmwf_aifs_ens",
                "ecmwf_aifs_ens_sampled_2t_6h_v1",
                "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            ),
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "openmeteo_ecmwf_ifs9_anchor_localday_high",
            ),
        ):
            conn.execute(
                """
                INSERT INTO raw_forecast_artifacts (
                    source_id, product_id, data_version, artifact_path, product_metadata_json
                ) VALUES (?, ?, ?, '/tmp/artifact', ?)
                """,
                (
                    source_id,
                    product_id,
                    data_version,
                    json.dumps({"cities": ["Paris"], "target_dates": ["2026-06-09"]}),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(db)
    paris = next(row for row in plan.rows if row.city == "Paris")

    assert paris.posterior_count == 1
    assert paris.readiness_count == 0
    assert paris.covered is False
    assert paris.can_seed is True


def test_current_target_plan_does_not_seed_after_local_target_day_starts(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE market_events
            SET target_date = '2026-06-07'
            WHERE city = 'London'
            """
        )
        conn.execute(
            """
            UPDATE source_run_coverage
            SET target_local_date = '2026-06-07'
            WHERE city = 'London'
            """
        )
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            """,
            (json.dumps({"cities": ["London"], "target_dates": ["2026-06-07"]}),),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 1, 0, tzinfo=timezone.utc),
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.covered is False
    assert london.day0_observed_extreme_required is True
    assert london.can_seed is False
    assert plan.day0_observed_extreme_required_count == 1
    assert "REPLACEMENT_CURRENT_TARGET_PLAN_DAY0_OBSERVED_EXTREME_REQUIRED" in plan.reason_codes


def test_current_target_plan_blocks_when_source_run_dependency_schema_is_missing(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE market_events (
                event_id INTEGER PRIMARY KEY,
                market_slug TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                token_id TEXT,
                range_label TEXT
            );
            CREATE TABLE forecast_posteriors (
                posterior_id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                data_version TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                trade_authority_status TEXT NOT NULL,
                training_allowed INTEGER NOT NULL
            );
            CREATE TABLE readiness_state (
                readiness_id TEXT PRIMARY KEY,
                strategy_key TEXT NOT NULL,
                provenance_json TEXT NOT NULL
            );
            CREATE TABLE source_run_coverage (
                coverage_id TEXT PRIMARY KEY,
                source_run_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                city TEXT NOT NULL,
                target_local_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                data_version TEXT NOT NULL,
                completeness_status TEXT NOT NULL,
                readiness_status TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE raw_forecast_artifacts (
                artifact_id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                data_version TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                product_metadata_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO market_events (
                market_slug, city, target_date, temperature_metric, token_id, range_label
            ) VALUES ('slug', 'Madrid', '2026-06-09', 'high', 'token', '30°C')
            """
        )
        conn.execute(
            """
            INSERT INTO source_run_coverage (
                coverage_id, source_run_id, source_id, city, target_local_date,
                temperature_metric, data_version, completeness_status, readiness_status,
                computed_at, recorded_at
            ) VALUES (
                'coverage', 'baseline-current', 'ecmwf_open_data', 'Madrid',
                '2026-06-09', 'high', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
                'COMPLETE', 'LIVE_ELIGIBLE',
                '2026-06-07T08:00:00+00:00', '2026-06-07T08:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, product_id, data_version, city, target_date,
                temperature_metric, trade_authority_status, training_allowed
            ) VALUES (
                'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor',
                'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1',
                'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1',
                'Madrid', '2026-06-09', 'high', 'SHADOW_VETO_ONLY', 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO readiness_state (
                readiness_id, strategy_key, provenance_json
            ) VALUES (?, ?, ?)
            """,
            (
                "ready-old",
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
                json.dumps({"city": "Madrid", "target_date": "2026-06-09", "temperature_metric": "high"}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(db)

    assert plan.status == "BLOCKED"
    assert plan.reason_codes == ("REPLACEMENT_CURRENT_TARGET_PLAN_SOURCE_RUN_DEPENDENCY_SCHEMA_MISSING",)
