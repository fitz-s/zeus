# Created: 2026-06-06
# Last reused/audited: 2026-07-15
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-07; last_reused=2026-07-01
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
    _day0_observation_lag_reason,
    _latest_authorized_day0_fact,
    build_replacement_forecast_current_target_plan,
    replacement_forecast_download_plan_from_current_targets,
)
from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
)


def test_day0_observation_hwm_invalidates_older_conditioning() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT,
            target_date TEXT,
            source TEXT,
            station_id TEXT,
            temp_unit TEXT,
            imported_at TEXT,
            local_timestamp TEXT,
            utc_timestamp TEXT,
            running_max REAL,
            running_min REAL,
            authority TEXT,
            training_allowed INTEGER,
            causality_status TEXT,
            source_role TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO observation_instants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "Paris",
            "2026-07-10",
            "wu_icao_history",
            "LFPB",
            "C",
            "2026-07-10T11:05:00+00:00",
            "2026-07-10T13:00:00+02:00",
            "2026-07-10T11:00:00+00:00",
            32.0,
            20.0,
            "VERIFIED",
            1,
            "OK",
            "historical_hourly",
        ),
    )
    reason = _day0_observation_lag_reason(
        conn,
        city="Paris",
        target_date="2026-07-10",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
        posterior_provenance_json=json.dumps(
            {"day0_conditioning": {"observation_time": "2026-07-10T10:00:00+00:00"}}
        ),
    )
    assert reason is not None
    assert reason.startswith("basis=day0_observation_hwm_lag")


def test_day0_observation_without_import_clock_is_not_live_visible() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT, target_date TEXT, source TEXT, station_id TEXT,
            temp_unit TEXT, local_timestamp TEXT, utc_timestamp TEXT,
            running_max REAL, running_min REAL, authority TEXT,
            training_allowed INTEGER, causality_status TEXT, source_role TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "Paris", "2026-07-10", "wu_icao_history", "LFPB", "C",
            "2026-07-10T13:00:00+02:00", "2026-07-10T11:00:00+00:00",
            32.0, 20.0, "VERIFIED", 1, "OK", "historical_hourly",
        ),
    )

    assert _latest_authorized_day0_fact(
        conn,
        city="Paris",
        target_date="2026-07-10",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
    ) is None
    conn.close()


def test_day0_hwm_accepts_authorized_durable_fast_observation_event() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE opportunity_events (
            event_id TEXT,
            event_type TEXT,
            available_at TEXT,
            received_at TEXT,
            created_at TEXT,
            payload_json TEXT
        )
        """
    )
    payload = {
        "city": "Busan",
        "target_date": "2026-07-11",
        "metric": "high",
        "settlement_source": "aviationweather_metar",
        "station_id": "RKPK",
        "observation_time": "2026-07-10T15:00:00+00:00",
        "raw_value": 25.0,
        "rounded_value": 25,
        "high_so_far": 25.0,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    conn.execute(
        "INSERT INTO opportunity_events VALUES (?, ?, ?, ?, ?, ?)",
        (
            "day0-busan",
            "DAY0_EXTREME_UPDATED",
            "2026-07-10T15:04:00+00:00",
            "2026-07-10T15:04:01+00:00",
            "2026-07-10T15:04:01+00:00",
            json.dumps(payload),
        ),
    )
    for minute in range(8):
        available_second = 30 + minute
        older_observation_later_arrival = {
            **payload,
            "settlement_source": "wu_icao_history",
            "observation_time": f"2026-07-10T14:{minute:02d}:00+00:00",
            "observation_available_at": (
                f"2026-07-10T15:04:{available_second:02d}+00:00"
            ),
            "raw_value": 24.0,
            "rounded_value": 24,
            "high_so_far": 24.0,
        }
        conn.execute(
            "INSERT INTO opportunity_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"day0-busan-older-observation-later-arrival-{minute}",
                "DAY0_EXTREME_UPDATED",
                f"2026-07-10T15:04:{available_second:02d}+00:00",
                f"2026-07-10T15:04:{available_second + 1:02d}+00:00",
                f"2026-07-10T15:04:{available_second + 1:02d}+00:00",
                json.dumps(older_observation_later_arrival),
            ),
        )

    fact = _latest_authorized_day0_fact(
        conn,
        city="Busan",
        target_date="2026-07-11",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 10, 15, 5, tzinfo=timezone.utc),
    )
    reason = _day0_observation_lag_reason(
        conn,
        city="Busan",
        target_date="2026-07-11",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 10, 15, 5, tzinfo=timezone.utc),
        posterior_provenance_json=json.dumps({}),
    )

    assert fact is not None
    assert fact["observed_extreme_native"] == 25.0
    assert fact["source"] == "durable_day0_event:aviationweather_metar"
    assert fact["unit"] == "C"
    assert reason is not None
    assert reason.startswith("basis=day0_observation_hwm_lag")


def test_day0_settlement_certainty_excludes_unconfirmed_fast_channel() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE opportunity_events (
            event_id TEXT,
            event_type TEXT,
            available_at TEXT,
            received_at TEXT,
            created_at TEXT,
            payload_json TEXT
        )
        """
    )
    authority = {
        "city": "Karachi",
        "target_date": "2026-07-15",
        "metric": "high",
        "station_id": "OPKC",
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    for event_id, source, observed_at, value in (
        ("wu", "wu_api", "2026-07-15T03:00:00+00:00", 29.0),
        ("fast", "aviationweather_metar", "2026-07-15T03:30:00+00:00", 30.0),
    ):
        payload = {
            **authority,
            "settlement_source": source,
            "observation_time": observed_at,
            "observation_available_at": observed_at,
            "raw_value": value,
            "rounded_value": int(value),
            "high_so_far": value,
            "settlement_unit": "C",
        }
        conn.execute(
            "INSERT INTO opportunity_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                event_id,
                "DAY0_EXTREME_UPDATED",
                observed_at,
                observed_at,
                observed_at,
                json.dumps(payload),
            ),
        )

    physical = _latest_authorized_day0_fact(
        conn,
        city="Karachi",
        target_date="2026-07-15",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 15, 3, 35, tzinfo=timezone.utc),
    )
    settlement = _latest_authorized_day0_fact(
        conn,
        city="Karachi",
        target_date="2026-07-15",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 15, 3, 35, tzinfo=timezone.utc),
        require_settlement_channel=True,
    )

    assert physical is not None
    assert physical["observed_extreme_native"] == 30.0
    assert settlement is not None
    assert settlement["observed_extreme_native"] == 29.0
    assert settlement["observation_source"] == "wu_api"
    conn.close()


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
            CREATE TABLE source_run (
                source_run_id TEXT PRIMARY KEY,
                source_cycle_time TEXT
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
        conn.execute(
            """
            CREATE TABLE raw_model_forecasts (
                raw_model_forecast_id INTEGER PRIMARY KEY,
                city TEXT NOT NULL,
                metric TEXT NOT NULL,
                target_date TEXT NOT NULL,
                model TEXT NOT NULL,
                forecast_value_c REAL NOT NULL,
                lead_days INTEGER,
                source_cycle_time TEXT NOT NULL,
                captured_at TEXT,
                endpoint TEXT NOT NULL
            )
            """
        )
        for city in ("Madrid", "London", "Paris"):
            conn.execute(
                """
                INSERT INTO source_run VALUES (
                    ?,
                    '2026-06-07T06:00:00+00:00'
                )
                """,
                (f"baseline-current-{city}",),
            )
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
                            "source_cycle_time": "2026-06-07T06:00:00+00:00",
                            "source_run_id": f"openmeteo-current-{city}",
                        }
                    ),
                ),
            )
            if city in {"London", "Paris"}:
                conn.execute(
                    """
                    INSERT INTO raw_model_forecasts (
                        city, metric, target_date, model, forecast_value_c, lead_days,
                        source_cycle_time, captured_at, endpoint
                    ) VALUES (?, 'high', '2026-06-09', 'gfs_global', 21.0, 2,
                        '2026-06-07T06:00:00+00:00',
                        '2026-06-07T08:00:00+00:00',
                        'single_runs')
                    """,
                    (city,),
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
    assert download_plan["fusion_current_value_missing_targets"] == []


def test_current_target_plan_reseeds_old_probability_semantics(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        for ddl in (
            "ALTER TABLE forecast_posteriors ADD COLUMN q_lcb_json TEXT",
            "ALTER TABLE forecast_posteriors ADD COLUMN q_ucb_json TEXT",
            "ALTER TABLE forecast_posteriors ADD COLUMN provenance_json TEXT",
            "ALTER TABLE forecast_posteriors ADD COLUMN source_cycle_time TEXT",
            "ALTER TABLE forecast_posteriors ADD COLUMN computed_at TEXT",
        ):
            conn.execute(ddl)
        conn.execute(
            """
            UPDATE forecast_posteriors
               SET q_lcb_json='{}', q_ucb_json='{}',
                   source_cycle_time='2026-06-07T06:00:00+00:00',
                   computed_at='2026-06-07T10:00:00+00:00',
                   provenance_json=?
             WHERE city='Paris'
            """,
            (
                json.dumps(
                    {
                        "q_lcb_basis": "fused_center_bootstrap_p05",
                        "bayes_precision_fusion": {
                            "current_evidence_shape": {
                                "semantics_revision": "older-law"
                            }
                        },
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    stale_plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    stale = next(row for row in stale_plan.rows if row.city == "Paris")
    assert stale.covered is False
    assert stale.can_seed is True

    conn = sqlite3.connect(db)
    try:
        provenance = json.loads(
            conn.execute(
                "SELECT provenance_json FROM forecast_posteriors WHERE city='Paris'"
            ).fetchone()[0]
        )
        provenance["bayes_precision_fusion"]["current_evidence_shape"][
            "semantics_revision"
        ] = CURRENT_EVIDENCE_SEMANTICS_REVISION
        conn.execute(
            "UPDATE forecast_posteriors SET provenance_json=? WHERE city='Paris'",
            (json.dumps(provenance),),
        )
        conn.commit()
    finally:
        conn.close()

    current_plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    current = next(row for row in current_plan.rows if row.city == "Paris")
    assert current.covered is True
    assert current.can_seed is False


def test_current_target_plan_reseeds_same_cycle_late_used_model_input(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("ALTER TABLE forecast_posteriors ADD COLUMN source_cycle_time TEXT")
        conn.execute("ALTER TABLE forecast_posteriors ADD COLUMN computed_at TEXT")
        conn.execute("ALTER TABLE forecast_posteriors ADD COLUMN provenance_json TEXT")
        conn.execute(
            "UPDATE forecast_posteriors SET source_cycle_time=?, computed_at=?, "
            "provenance_json=? WHERE city='Paris'",
            (
                "2026-06-07T06:00:00+00:00",
                "2026-06-07T08:30:00+00:00",
                json.dumps(
                        {
                            "used_models": ["gfs_global"],
                            "q_lcb_basis": "fused_center_bootstrap_p05",
                            "bayes_precision_fusion": {
                                "current_evidence_shape": {
                                    "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION
                                }
                            },
                        }
                ),
            ),
        )
        conn.execute(
            "UPDATE raw_model_forecasts SET captured_at=? WHERE city='Paris' "
            "AND model='gfs_global'",
            ("2026-06-07T09:00:00+00:00",),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    paris = next(row for row in plan.rows if row.city == "Paris")

    assert paris.input_lag_reason is not None
    assert "same_cycle_late_input" in paris.input_lag_reason
    assert paris.covered is False
    assert paris.can_seed is True


def test_current_target_plan_does_not_seed_when_fusion_current_values_are_missing(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM raw_model_forecasts WHERE city = 'London'")
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    download_plan = replacement_forecast_download_plan_from_current_targets(plan)
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 1
    assert london.fusion_current_value_count == 0
    assert london.missing_openmeteo_manifest is False
    assert london.missing_fusion_current_values is True
    assert london.can_seed is False
    assert plan.can_seed_count == 0
    assert plan.missing_fusion_current_values_count == 1
    assert "REPLACEMENT_CURRENT_TARGET_PLAN_MISSING_FUSION_CURRENT_VALUES" in plan.reason_codes
    assert [row["city"] for row in download_plan["fusion_current_value_missing_targets"]] == ["London"]
    assert download_plan["seedable_targets"] == []


def test_current_target_plan_seeds_when_openmeteo_cycle_outruns_lagging_baseline(tmp_path) -> None:
    """Regression: baseline (ECMWF-Open-Data, 00Z/12Z cadence) can lag behind a
    finer-cadence (00/06/12/18Z) openmeteo/BAYES_PRECISION_FUSION anchor manifest. The
    fusion current-value ceiling must be checked against the OPENMETEO MANIFEST'S OWN
    resolved cycle, not the baseline's cycle -- otherwise a scope with real captured
    fusion rows at the newer openmeteo cycle is wrongly blocked (count 0 ->
    missing_fusion_current_values -> can_seed False) even though the data is genuinely
    servable at the manifest's own resolved cycle."""
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        # London's OpenMeteo anchor manifest resolves to an 18Z cycle -- newer than the
        # baseline's 06Z source_run cycle (baseline has not published its next cycle yet).
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_cycle_time": "2026-06-07T18:00:00+00:00",
                        "source_run_id": "openmeteo-18z-London",
                    }
                ),
            ),
        )
        # The captured fusion current-value row exists ONLY at the newer 18Z cycle -- the
        # baseline's 06Z cycle (the pre-fix ceiling) has nothing at or before it.
        conn.execute("DELETE FROM raw_model_forecasts WHERE city = 'London'")
        conn.execute(
            """
            INSERT INTO raw_model_forecasts (
                city, metric, target_date, model, forecast_value_c, lead_days,
                source_cycle_time, captured_at, endpoint
            ) VALUES ('London', 'high', '2026-06-09', 'gfs_global', 21.0, 0,
                '2026-06-07T18:00:00+00:00',
                '2026-06-07T19:00:00+00:00',
                'single_runs')
            """
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 19, 30, tzinfo=timezone.utc),
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.baseline_source_cycle_time == "2026-06-07T06:00:00+00:00"
    assert london.openmeteo_manifest_count == 1
    assert london.fusion_current_value_count > 0
    assert london.missing_fusion_current_values is False
    assert london.can_seed is True


def test_current_target_plan_still_blocks_when_no_row_at_openmeteo_resolved_cycle(tmp_path) -> None:
    """Invariant: even with the manifest's-own-cycle fix, a scope with NO captured fusion
    row at (or before) the openmeteo manifest's resolved cycle must still be blocked. This
    guards against the fix over-admitting -- e.g. degenerating into an unconditional pass
    -- by proving the ceiling semantics still exclude a row from a cycle strictly newer
    than the manifest's own resolved cycle."""
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_cycle_time": "2026-06-07T18:00:00+00:00",
                        "source_run_id": "openmeteo-18z-London",
                    }
                ),
            ),
        )
        # Only a row from the NEXT cycle after the manifest's resolved 18Z cycle exists --
        # strictly newer than the ceiling under either the old (baseline) or new (manifest)
        # resolved cycle, so it must not be servable under the ceiling semantics either way.
        conn.execute("DELETE FROM raw_model_forecasts WHERE city = 'London'")
        conn.execute(
            """
            INSERT INTO raw_model_forecasts (
                city, metric, target_date, model, forecast_value_c, lead_days,
                source_cycle_time, captured_at, endpoint
            ) VALUES ('London', 'high', '2026-06-09', 'gfs_global', 21.0, 0,
                '2026-06-08T00:00:00+00:00',
                '2026-06-08T01:00:00+00:00',
                'single_runs')
            """
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 19, 30, tzinfo=timezone.utc),
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 1
    assert london.fusion_current_value_count == 0
    assert london.missing_fusion_current_values is True
    assert london.can_seed is False


def test_current_target_plan_can_require_openmeteo_manifest_cycle(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
        required_openmeteo_source_cycle_time="2026-06-07T12:00:00+00:00",
    )
    london = next(row for row in plan.rows if row.city == "London")
    paris = next(row for row in plan.rows if row.city == "Paris")

    assert london.openmeteo_manifest_count == 0
    assert london.missing_openmeteo_manifest is True
    assert london.can_seed is False
    assert paris.openmeteo_manifest_count == 0
    assert paris.covered is False
    assert plan.missing_openmeteo_manifest_count >= 2


def test_current_target_plan_requires_openmeteo_cycle_matching_each_baseline_source_run(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE source_run
            SET source_cycle_time = '2026-06-07T12:00:00+00:00'
            WHERE source_run_id = 'baseline-current-London'
            """
        )
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "source_run_id": "openmeteo-06z-London",
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 30, tzinfo=timezone.utc),
    )
    download_plan = replacement_forecast_download_plan_from_current_targets(plan)
    london = next(row for row in plan.rows if row.city == "London")

    assert london.baseline_source_cycle_time == "2026-06-07T12:00:00+00:00"
    assert london.openmeteo_manifest_count == 0
    assert london.missing_openmeteo_manifest is True
    assert "London" in [row["city"] for row in download_plan["openmeteo_download_targets"]]


def test_current_target_plan_explicit_cycle_currency_overrides_stale_baseline_cycle(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE source_run
            SET source_cycle_time = '2026-06-07T12:00:00+00:00'
            WHERE source_run_id = 'baseline-current-London'
            """
        )
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_cycle_time": "2026-06-07T12:00:00+00:00",
                        "source_run_id": "openmeteo-12z-London",
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 18, 30, tzinfo=timezone.utc),
        required_openmeteo_source_cycle_time="2026-06-07T18:00:00+00:00",
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.baseline_source_cycle_time == "2026-06-07T12:00:00+00:00"
    assert london.openmeteo_manifest_count == 0
    assert london.missing_openmeteo_manifest is True
    assert london.can_seed is False


def test_current_target_plan_rejects_openmeteo_manifest_without_target_day_samples(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    payload = tmp_path / "london_partial_payload.json"
    payload.write_text(
        json.dumps(
            {
                "hourly": {
                    "time": ["2026-06-08T00:00", "2026-06-08T01:00"],
                    "temperature_2m": [12.0, 13.0],
                }
            }
        ),
        encoding="utf-8",
    )
    precision = tmp_path / "precision.json"
    precision.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
                        "openmeteo_endpoint": "single_runs_api",
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "forecast_hours": 120,
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "source_run_id": "openmeteo-current-London",
                        "openmeteo_payload_json": str(payload),
                        "precision_metadata_json": str(precision),
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
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 0
    assert london.missing_openmeteo_manifest is True
    assert london.can_seed is False


def test_current_target_plan_counts_openmeteo_manifest_with_target_day_samples(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    payload = tmp_path / "london_covering_payload.json"
    payload.write_text(
        json.dumps(
            {
                "hourly": {
                    "time": ["2026-06-09T00:00", "2026-06-09T12:00"],
                    "temperature_2m": [14.0, 18.0],
                }
            }
        ),
        encoding="utf-8",
    )
    precision = tmp_path / "precision.json"
    precision.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
                        "openmeteo_endpoint": "single_runs_api",
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "forecast_hours": 120,
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "source_run_id": "openmeteo-current-London",
                        "openmeteo_payload_json": str(payload),
                        "precision_metadata_json": str(precision),
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
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 1
    assert london.can_seed is True


def test_current_target_plan_counts_meta_stamped_horizon_manifest_with_target_day_samples(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    payload = tmp_path / "london_meta_stamped_payload.json"
    payload.write_text(
        json.dumps(
            {
                "hourly": {
                    "time": ["2026-06-08T12:00", "2026-06-09T00:00", "2026-06-09T12:00"],
                    "temperature_2m": [13.0, 14.0, 18.0],
                }
            }
        ),
        encoding="utf-8",
    )
    precision = tmp_path / "precision.json"
    precision.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
                        "openmeteo_endpoint": "standard_api_meta_stamped",
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-08",
                        "target_dates": ["2026-06-08"],
                        "forecast_hours": 120,
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "source_run_id": "openmeteo-current-London",
                        "openmeteo_payload_json": str(payload),
                        "precision_metadata_json": str(precision),
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
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 1
    assert london.can_seed is True


def test_current_target_plan_counts_single_runs_horizon_manifest_with_target_day_samples(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    payload = tmp_path / "london_single_runs_horizon_payload.json"
    payload.write_text(
        json.dumps(
            {
                "hourly": {
                    "time": ["2026-06-08T12:00", "2026-06-09T00:00", "2026-06-09T12:00"],
                    "temperature_2m": [13.0, 14.0, 18.0],
                }
            }
        ),
        encoding="utf-8",
    )
    precision = tmp_path / "precision.json"
    precision.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
                        "openmeteo_endpoint": "single_runs_api",
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-08",
                        "target_dates": ["2026-06-08"],
                        "forecast_hours": 120,
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "source_run_id": "openmeteo-current-London",
                        "openmeteo_payload_json": str(payload),
                        "precision_metadata_json": str(precision),
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
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 1
    assert london.can_seed is True


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
