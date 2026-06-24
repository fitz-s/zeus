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
from pathlib import Path

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
                training_allowed INTEGER NOT NULL,
                runtime_layer TEXT NOT NULL DEFAULT 'live'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE readiness_state (
                readiness_id TEXT PRIMARY KEY,
                strategy_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'READY',
                dependency_json TEXT NOT NULL DEFAULT '{}',
                provenance_json TEXT NOT NULL,
                expires_at TEXT
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
                'openmeteo_ecmwf_ifs9_bayes_fusion',
                'openmeteo_ecmwf_ifs9_bayes_fusion_v1',
                'openmeteo_ecmwf_ifs9_bayes_fusion_high_v1',
                'Paris', '2026-06-09', 'high',
                '{"baseline_b0":"baseline-current-Paris","openmeteo_ifs9_anchor":"openmeteo-current-Paris"}',
                'live', 0
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
                'openmeteo_ecmwf_ifs9_bayes_fusion',
                'openmeteo_ecmwf_ifs9_bayes_fusion_v1',
                'openmeteo_ecmwf_ifs9_bayes_fusion_high_v1',
                'Madrid', '2026-06-09', 'high',
                '{"baseline_b0":"baseline-stale-Madrid","openmeteo_ifs9_anchor":"openmeteo-current-Madrid"}',
                'live', 0
            )
            """
        )
        conn.execute(
            """
                INSERT INTO readiness_state (
                    readiness_id, strategy_key, status, dependency_json, provenance_json
                ) VALUES (?, ?, 'READY', ?, ?)
            """,
            (
                "ready-paris",
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                json.dumps(
                    {
                        "dependencies": [
                            {"role": "baseline_b0", "source_run_id": "baseline-current-Paris"},
                            {"role": "openmeteo_ifs9_anchor", "source_run_id": "openmeteo-current-Paris"},
                        ]
                    }
                ),
                json.dumps({"city": "Paris", "target_date": "2026-06-09", "temperature_metric": "high"}),
            ),
        )
        conn.execute(
            """
                INSERT INTO readiness_state (
                    readiness_id, strategy_key, status, dependency_json, provenance_json
                ) VALUES (?, ?, 'READY', ?, ?)
            """,
            (
                "ready-madrid-stale",
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                json.dumps(
                    {
                        "dependencies": [
                            {"role": "baseline_b0", "source_run_id": "baseline-stale-Madrid"},
                            {"role": "openmeteo_ifs9_anchor", "source_run_id": "openmeteo-current-Madrid"},
                        ]
                    }
                ),
                json.dumps({"city": "Madrid", "target_date": "2026-06-09", "temperature_metric": "high"}),
            ),
        )
        # An artifact only counts as coverage if its file is actually on disk (DB<->disk
        # provenance antibody). Write a real file for the "present" London artifacts.
        present_artifact = Path(path).parent / "present_artifact.grib2"
        present_artifact.write_bytes(b"GRIB")
        for city in ("London", "Paris"):
            conn.execute(
                """
                INSERT INTO raw_forecast_artifacts (
                    source_id, product_id, data_version, artifact_path, product_metadata_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "openmeteo_ecmwf_ifs_9km",
                    "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                    "openmeteo_ecmwf_ifs9_anchor_localday_high",
                    str(present_artifact),
                    json.dumps(
                        {
                            "city": city,
                            "cities": [city],
                            "target_date": "2026-06-09",
                            "target_dates": ["2026-06-09"],
                            "source_run_id": f"openmeteo-current-{city}",
                        }
                    ),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def test_current_target_plan_classifies_covered_seedable_and_missing_manifest_targets(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)

    # Fixed evaluation time before the 2026-06-09 target so day0 logic does not lock the targets
    # (the fixture dates are static; real wall-clock has since advanced past them).
    now_utc = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    plan = build_replacement_forecast_current_target_plan(db, now_utc=now_utc)
    download_plan = replacement_forecast_download_plan_from_current_targets(plan)

    assert plan.status == "CURRENT_TARGETS_MISSING_REPLACEMENT_COVERAGE"
    assert plan.target_count == 3
    assert plan.covered_count == 1
    # Seeding gates on the OpenMeteo anchor manifest and coverage requires the same
    # anchor source_run_id in both posterior and readiness. London (manifest present,
    # no posterior) is seedable; Madrid (no OpenMeteo manifest) is the download target.
    assert plan.can_seed_count == 1
    assert plan.missing_openmeteo_manifest_count == 1
    assert [row["city"] for row in download_plan["seedable_targets"]] == ["London"]
    assert [row["city"] for row in download_plan["openmeteo_download_targets"]] == ["Madrid"]


def test_current_target_plan_reseeds_when_openmeteo_anchor_advances_under_same_baseline(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    newer_artifact = Path(db).parent / "newer_paris_artifact.json"
    newer_artifact.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            INSERT INTO raw_forecast_artifacts (
                source_id, product_id, data_version, artifact_path, product_metadata_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "openmeteo_ecmwf_ifs9_anchor_localday_high",
                str(newer_artifact),
                json.dumps(
                    {
                        "city": "Paris",
                        "cities": ["Paris"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "requested_source_available_at": "2026-06-07T12:00:00+00:00",
                        "source_run_id": "openmeteo-newer-Paris",
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    paris = next(row for row in plan.rows if row.city == "Paris")

    assert paris.baseline_source_run_id == "baseline-current-Paris"
    assert paris.openmeteo_source_run_id == "openmeteo-newer-Paris"
    assert paris.posterior_count == 0
    assert paris.readiness_count == 0
    assert paris.covered is False
    assert paris.can_seed is True


def test_current_target_plan_does_not_treat_blocked_replacement_readiness_as_covered(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE readiness_state SET status = 'BLOCKED' WHERE readiness_id = 'ready-paris'")
        present_artifact = Path(db).parent / "present_artifact.grib2"  # written by _create_db
        conn.execute(
            """
            INSERT INTO raw_forecast_artifacts (
                source_id, product_id, data_version, artifact_path, product_metadata_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "openmeteo_ecmwf_ifs9_anchor_localday_high",
                str(present_artifact),
                json.dumps(
                    {
                        "city": "Paris",
                        "cities": ["Paris"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_run_id": "openmeteo-current-Paris",
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    paris = next(row for row in plan.rows if row.city == "Paris")

    assert paris.posterior_count == 1
    assert paris.readiness_count == 0
    assert paris.covered is False
    assert paris.can_seed is True


def test_current_target_plan_ignores_artifact_rows_whose_file_is_deleted(tmp_path) -> None:
    """DB<->disk provenance relationship (Fitz #4): when a raw_forecast_artifacts FILE is
    deleted but its DB row survives, the plan must NOT keep reporting the target as covered/
    seedable. Otherwise the download-skip gate believes raw inputs are present and never
    re-fetches, while disk-based seed discovery finds nothing -> the ~30h zero-trade stall.

    Models the real incident exactly: London is seedable with files on disk; delete the file
    (leave the DB row) and London must flip to missing_openmeteo_manifest so the gate re-downloads.

    The DB<->disk provenance invariant (a deleted file flips the target back
    to needs-download, so the gate re-fetches) is preserved via the OpenMeteo
    manifest.
    """
    db = tmp_path / "forecasts.db"
    _create_db(db)
    now_utc = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)

    # Baseline: London has artifacts on disk -> seedable (openmeteo present), nothing missing.
    before = build_replacement_forecast_current_target_plan(db, now_utc=now_utc)
    london_before = next(row for row in before.rows if row.city == "London")
    assert london_before.can_seed is True
    assert london_before.openmeteo_manifest_count == 1
    assert london_before.missing_openmeteo_manifest is False

    # The cleanup deletes the GRIB/manifest FILE but the DB row survives (dangling pointer).
    present_artifact = Path(db).parent / "present_artifact.grib2"
    present_artifact.unlink()

    after = build_replacement_forecast_current_target_plan(db, now_utc=now_utc)
    london_after = next(row for row in after.rows if row.city == "London")
    assert london_after.openmeteo_manifest_count == 0, "a deleted artifact file must not count as coverage"
    assert london_after.missing_openmeteo_manifest is True, "gate must see missing -> re-download"
    assert london_after.can_seed is False
    assert after.missing_openmeteo_manifest_count >= 1


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
        min_target_date="2026-06-07",
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
                'openmeteo_ecmwf_ifs9_bayes_fusion',
                'openmeteo_ecmwf_ifs9_bayes_fusion_v1',
                'openmeteo_ecmwf_ifs9_bayes_fusion_high_v1',
                'Madrid', '2026-06-09', 'high', 'live', 0
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
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                json.dumps({"city": "Madrid", "target_date": "2026-06-09", "temperature_metric": "high"}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(db)

    assert plan.status == "BLOCKED"
    assert plan.reason_codes == ("REPLACEMENT_CURRENT_TARGET_PLAN_SOURCE_RUN_DEPENDENCY_SCHEMA_MISSING",)
