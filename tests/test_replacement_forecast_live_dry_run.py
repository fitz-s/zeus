# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect replacement forecast live dry-run switch readiness checks.
# Reuse: Run before changing replacement forecast simple-switch validation.
# Authority basis: Operator-directed safe simple-switch live readiness.
"""Replacement forecast live dry-run gate tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

from src.data.replacement_forecast_live_dry_run import (
    ReplacementForecastLiveDryRunInput,
    _current_target_coverage_inventory,
    build_replacement_forecast_live_dry_run_report,
)
from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
)
from src.data.replacement_forecast_bundle_reader import HIGH_DATA_VERSION
from src.data.replacement_forecast_live_switch_surface import REFIT_HANDOFF_FILE, REQUIRED_FORECAST_TABLES, REQUIRED_TRADE_TABLES, REQUIRED_WORLD_TABLES
from src.data.replacement_forecast_readiness import PRODUCT_ID, SOURCE_ID
from scripts.init_replacement_forecast_shadow_schema import (
    REPLACEMENT_SHADOW_TABLES,
    initialize_replacement_forecast_shadow_schema,
)


def _flags(*, shadow: bool = True, veto: bool = True, trade: bool = False) -> dict[str, bool]:
    return {
        SHADOW_FLAG: shadow,
        VETO_FLAG: veto,
        TRADE_AUTHORITY_FLAG: trade,
        KELLY_INCREASE_FLAG: False,
        DIRECTION_FLIP_FLAG: False,
    }


def _write_current_files(root) -> None:
    (root / "config").mkdir(parents=True)
    (root / "state").mkdir(parents=True)
    (root / "docs" / "operations").mkdir(parents=True)
    (root / "config" / "settings.json").write_text("{}", encoding="utf-8")
    (root / "config" / "cities.json").write_text("[]", encoding="utf-8")
    (root / "config" / "source_release_calendar.yaml").write_text("{}\n", encoding="utf-8")
    (root / "docs" / "operations" / "current_source_validity.md").write_text("Status: CURRENT_FOR_LIVE\n", encoding="utf-8")
    (root / "docs" / "operations" / "current_data_state.md").write_text("Status: CURRENT_FOR_LIVE\n", encoding="utf-8")


def _write_refit_handoff(root) -> None:
    path = root / REFIT_HANDOFF_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        __import__("json").dumps(
            {
                "schema_version": "replacement_forecast_refit_handoff_v1",
                "status": "REFIT_HANDOFF_READY",
                "ready_for_product_refit": True,
                "live_promotion_allowed": False,
                "training_scope": "replacement_product_specific_only",
                "baseline_calibration_reused": False,
                "metric": "high",
                "source_family": "derived_posterior",
                "source_id": SOURCE_ID,
                "product_id": PRODUCT_ID,
                "data_version": HIGH_DATA_VERSION,
                "calibration_method": "soft_anchor_product_specific_nested_refit",
                "emos_cell_key": f"Shanghai|JJA|high|derived_posterior|{SOURCE_ID}|{PRODUCT_ID}|{HIGH_DATA_VERSION}",
                "refit_decision": {
                    "status": "PRODUCT_SPECIFIC_REFIT_READY",
                    "reason_codes": ["REPLACEMENT_REFIT_PRODUCT_SPECIFIC_EVIDENCE_READY"],
                    "data_refit_required": True,
                    "emos_replacement_ready": True,
                    "product_specific_training_allowed": True,
                    "live_promotion_allowed": False,
                    "missing_evidence": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_settings_flags(root, *, shadow: bool, veto: bool) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "settings.json").write_text(
        (
            "{\n"
            '  "feature_flags": {\n'
            f'    "{SHADOW_FLAG}": {str(shadow).lower()},\n'
            f'    "{VETO_FLAG}": {str(veto).lower()},\n'
            f'    "{TRADE_AUTHORITY_FLAG}": false,\n'
            f'    "{KELLY_INCREASE_FLAG}": false,\n'
            f'    "{DIRECTION_FLIP_FLAG}": false\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )


def _write_settings_flags_with_refit_path(root, *, shadow: bool, veto: bool, refit_handoff_path: str) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "settings.json").write_text(
        __import__("json").dumps(
            {
                "feature_flags": {
                    SHADOW_FLAG: shadow,
                    VETO_FLAG: veto,
                    TRADE_AUTHORITY_FLAG: False,
                    KELLY_INCREASE_FLAG: False,
                    DIRECTION_FLIP_FLAG: False,
                },
                "replacement_forecast_shadow": {
                    "refit_handoff_path": refit_handoff_path,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _create_empty_current_target_table(conn: sqlite3.Connection, table: str) -> bool:
    if table == "market_events":
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
        return True
    if table == "source_run_coverage":
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
        return True
    if table == "readiness_state":
        conn.execute(
            """
            CREATE TABLE readiness_state (
                readiness_id TEXT PRIMARY KEY,
                strategy_key TEXT NOT NULL,
                dependency_json TEXT NOT NULL DEFAULT '{}',
                provenance_json TEXT NOT NULL
            )
            """
        )
        return True
    if table == "forecast_posteriors":
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
                computed_at TEXT
            )
            """
        )
        return True
    return False


def _create_db(path, tables) -> None:
    conn = sqlite3.connect(path)
    try:
        for table in tables:
            if _create_empty_current_target_table(conn, table):
                continue
            if table == "raw_forecast_artifacts":
                conn.execute(
                    """
                    CREATE TABLE raw_forecast_artifacts (
                        artifact_id INTEGER PRIMARY KEY,
                        source_id TEXT NOT NULL,
                        product_id TEXT NOT NULL,
                        data_version TEXT NOT NULL,
                        source_cycle_time TEXT NOT NULL,
                        source_available_at TEXT NOT NULL,
                        captured_at TEXT NOT NULL,
                        artifact_path TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        byte_size INTEGER NOT NULL,
                        request_url TEXT,
                        request_params_json TEXT NOT NULL DEFAULT '{}',
                        artifact_metadata_json TEXT NOT NULL DEFAULT '{}',
                        trade_authority_status TEXT NOT NULL DEFAULT 'SHADOW_ONLY',
                        training_allowed INTEGER NOT NULL DEFAULT 0,
                        recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO raw_forecast_artifacts (
                        source_id, product_id, data_version, source_cycle_time,
                        source_available_at, captured_at, artifact_path, sha256, byte_size
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            "openmeteo_ecmwf_ifs_9km",
                            "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                            "openmeteo_ecmwf_ifs9_anchor_localday_high",
                            "2026-06-06T00:00:00+00:00",
                            "2026-06-06T07:00:00+00:00",
                            "2026-06-06T07:01:00+00:00",
                            "/tmp/openmeteo.json",
                            "a" * 64,
                            1,
                        ),
                        (
                            "ecmwf_aifs_ens",
                            "ecmwf_aifs_ens_sampled_2t_6h_v1",
                            "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
                            "2026-06-06T00:00:00+00:00",
                            "2026-06-06T07:00:00+00:00",
                            "2026-06-06T07:01:00+00:00",
                            "/tmp/aifs.grib",
                            "b" * 64,
                            1,
                        ),
                    ),
                )
            else:
                conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()


def _create_forecast_db_with_replacement_inventory(path) -> None:
    conn = sqlite3.connect(path)
    try:
        for table in REQUIRED_FORECAST_TABLES:
            if table in {"forecast_posteriors", "replacement_shadow_decisions"}:
                continue
            if table == "readiness_state":
                conn.execute(
                    """
                    CREATE TABLE readiness_state (
                        readiness_id TEXT PRIMARY KEY,
                        strategy_key TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        data_version TEXT NOT NULL,
                        status TEXT NOT NULL,
                        reason_codes_json TEXT NOT NULL,
                        dependency_json TEXT NOT NULL,
                        provenance_json TEXT NOT NULL,
                        computed_at TEXT NOT NULL,
                        recorded_at TEXT NOT NULL,
                        expires_at TEXT
                    )
                    """
                )
                continue
            if _create_empty_current_target_table(conn, table):
                continue
            if table == "raw_forecast_artifacts":
                conn.execute(
                    """
                    CREATE TABLE raw_forecast_artifacts (
                        artifact_id INTEGER PRIMARY KEY,
                        source_id TEXT NOT NULL,
                        product_id TEXT NOT NULL,
                        data_version TEXT NOT NULL,
                        source_cycle_time TEXT NOT NULL,
                        source_available_at TEXT NOT NULL,
                        captured_at TEXT NOT NULL,
                        artifact_path TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        byte_size INTEGER NOT NULL
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO raw_forecast_artifacts (
                        source_id, product_id, data_version, source_cycle_time,
                        source_available_at, captured_at, artifact_path, sha256, byte_size
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            "openmeteo_ecmwf_ifs_9km",
                            "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                            "openmeteo_ecmwf_ifs9_anchor_localday_high",
                            "2026-06-06T00:00:00+00:00",
                            "2026-06-06T07:00:00+00:00",
                            "2026-06-06T07:01:00+00:00",
                            "/tmp/openmeteo.json",
                            "a" * 64,
                            1,
                        ),
                        (
                            "ecmwf_aifs_ens",
                            "ecmwf_aifs_ens_sampled_2t_6h_v1",
                            "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
                            "2026-06-06T00:00:00+00:00",
                            "2026-06-06T07:00:00+00:00",
                            "2026-06-06T07:01:00+00:00",
                            "/tmp/aifs.grib",
                            "b" * 64,
                            1,
                        ),
                    ),
                )
                continue
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
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
                computed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE replacement_shadow_decisions (
                decision_id INTEGER PRIMARY KEY,
                posterior_id INTEGER NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                baseline_direction TEXT NOT NULL,
                allowed_direction TEXT NOT NULL,
                trade_authority_status TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                posterior_id, source_id, product_id, data_version, city, target_date,
                temperature_metric, dependency_source_run_ids_json, trade_authority_status, training_allowed, computed_at
            ) VALUES (
                7,
                'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor',
                ?,
                ?,
                'NYC',
                '2026-06-07',
                'high',
                '{"baseline_b0":"baseline-run"}',
                'SHADOW_VETO_ONLY',
                0,
                '2026-06-06T04:00:00+00:00'
            )
            """,
            (PRODUCT_ID, HIGH_DATA_VERSION),
        )
        conn.execute(
            """
            INSERT INTO replacement_shadow_decisions (
                decision_id, posterior_id, city, target_date, temperature_metric,
                baseline_direction, allowed_direction, trade_authority_status, recorded_at
            ) VALUES (
                9, 7, 'NYC', '2026-06-07', 'high',
                'buy_yes:warm', 'buy_yes:warm', 'SHADOW_VETO_ONLY',
                '2026-06-06T04:05:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO readiness_state (
                readiness_id, strategy_key, source_id, data_version, status,
                reason_codes_json, dependency_json, provenance_json, computed_at, recorded_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "replacement_readiness:test",
                SOURCE_ID,
                SOURCE_ID,
                HIGH_DATA_VERSION,
                "SHADOW_ONLY",
                "[]",
                json.dumps(
                    {
                        "dependencies": [
                            {
                                "role": "aifs_sampled_2t",
                                "artifact_id": 2,
                                "source_id": "ecmwf_aifs_ens",
                                "data_version": "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
                                "source_available_at": "2026-06-06T07:00:00+00:00",
                            },
                            {
                                "role": "openmeteo_ifs9_anchor",
                                "artifact_id": 1,
                                "source_id": "openmeteo_ecmwf_ifs_9km",
                                "data_version": "openmeteo_ecmwf_ifs9_anchor_localday_high",
                                "source_available_at": "2026-06-06T07:00:00+00:00",
                            },
                        ]
                    }
                ),
                json.dumps(
                    {
                        "city": "NYC",
                        "target_date": "2026-06-07",
                        "temperature_metric": "high",
                        "computed_at": "2026-06-06T04:00:00+00:00",
                    }
                ),
                "2026-06-06T04:00:00+00:00",
                "2026-06-06T04:00:01+00:00",
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_raw_artifact_lineage(path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO raw_forecast_artifacts (
                source_id, product_id, data_version, source_cycle_time,
                source_available_at, captured_at, artifact_path, sha256, byte_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "openmeteo_ecmwf_ifs_9km",
                    "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                    "openmeteo_ecmwf_ifs9_anchor_localday_high",
                    "2026-06-06T00:00:00+00:00",
                    "2026-06-06T07:00:00+00:00",
                    "2026-06-06T07:01:00+00:00",
                    "/tmp/openmeteo.json",
                    "a" * 64,
                    1,
                ),
                (
                    "ecmwf_aifs_ens",
                    "ecmwf_aifs_ens_sampled_2t_6h_v1",
                    "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
                    "2026-06-06T00:00:00+00:00",
                    "2026-06-06T07:00:00+00:00",
                    "2026-06-06T07:01:00+00:00",
                    "/tmp/aifs.grib",
                    "b" * 64,
                    1,
                ),
            ),
        )
        conn.commit()


def test_live_dry_run_blocks_missing_dbs_tables_facts_and_optional_dependencies(tmp_path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.json").write_text("{}", encoding="utf-8")

    report = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(root=tmp_path, runtime_flags=_flags(), optional_dependencies=("requests", "missing.module"))
    )

    assert report.ok is False
    assert report.status == "BLOCKED"
    assert "REPLACEMENT_SWITCH_MISSING_READ_FILES" in report.reason_codes
    assert "REPLACEMENT_SWITCH_MISSING_READ_TABLES" in report.reason_codes
    assert report.source_fact_status == "STALE_FOR_LIVE"
    assert report.data_fact_status == "STALE_FOR_LIVE"
    assert report.dependency_status["missing.module"].startswith("MISSING:")


def test_live_dry_run_ready_when_files_tables_facts_and_dependencies_are_present(tmp_path) -> None:
    _write_current_files(tmp_path)
    _write_refit_handoff(tmp_path)
    _create_db(tmp_path / "state" / "zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    report = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(root=tmp_path, runtime_flags=_flags(), optional_dependencies=("requests",))
    )

    assert report.ok is True
    assert report.reason_codes == ("REPLACEMENT_DRY_RUN_READY",)
    assert report.live_switch_report.simple_switch_ready is True
    assert report.refit_handoff_status == "READY"
    assert report.configured_refit_handoff_status == "READY"
    assert report.raw_artifact_lineage_status == "READY"
    assert report.raw_artifact_lineage_counts["openmeteo_ecmwf_ifs_9km"] == 1
    assert report.raw_artifact_lineage_counts["ecmwf_aifs_ens"] == 1


def test_live_dry_run_blocks_when_raw_artifact_lineage_is_missing_input_family(tmp_path) -> None:
    _write_current_files(tmp_path)
    _write_refit_handoff(tmp_path)
    _create_db(tmp_path / "state" / "zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    with sqlite3.connect(tmp_path / "state" / "zeus-forecasts.db") as conn:
        conn.execute("DELETE FROM raw_forecast_artifacts WHERE source_id = 'ecmwf_aifs_ens'")
        conn.commit()
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    report = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(root=tmp_path, runtime_flags=_flags(), optional_dependencies=("requests",))
    )

    assert report.ok is False
    assert report.raw_artifact_lineage_status == "MISSING_INPUT_FAMILY"
    assert report.raw_artifact_lineage_counts["openmeteo_ecmwf_ifs_9km"] == 1
    assert report.raw_artifact_lineage_counts["ecmwf_aifs_ens"] == 0
    assert "REPLACEMENT_DRY_RUN_RAW_ARTIFACT_LINEAGE_NOT_READY" in report.reason_codes


def test_live_dry_run_blocks_when_settings_refit_handoff_path_is_wrong(tmp_path) -> None:
    _write_current_files(tmp_path)
    _write_refit_handoff(tmp_path)
    _write_settings_flags_with_refit_path(
        tmp_path,
        shadow=True,
        veto=True,
        refit_handoff_path="state/replacement_forecast_shadow/missing_refit_handoff.json",
    )
    _create_db(tmp_path / "state" / "zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    report = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(root=tmp_path, runtime_flags=_flags(), optional_dependencies=("requests",))
    )

    assert report.ok is False
    assert report.refit_handoff_status == "READY"
    assert report.configured_refit_handoff_status == "MISSING"
    assert "missing_refit_handoff.json" in report.configured_refit_handoff_path
    assert "REPLACEMENT_DRY_RUN_CONFIGURED_REFIT_HANDOFF_MISSING" in report.reason_codes


def test_live_dry_run_reports_materialized_posterior_and_shadow_decision_inventory(tmp_path) -> None:
    _write_current_files(tmp_path)
    _write_refit_handoff(tmp_path)
    _create_forecast_db_with_replacement_inventory(tmp_path / "state" / "zeus-forecasts.db")
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    report = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(root=tmp_path, runtime_flags=_flags(), optional_dependencies=("requests",))
    )

    assert report.ok is True
    assert report.materialized_posterior_count == 1
    assert report.shadow_decision_count == 1
    assert report.latest_materialized_posterior["city"] == "NYC"
    assert report.latest_shadow_decision["allowed_direction"] == "buy_yes:warm"
    assert report.latest_readiness_artifact_status == "READY"
    assert report.latest_readiness_artifact_counts["aifs_sampled_2t"] == 1
    assert report.latest_readiness_artifact_counts["openmeteo_ifs9_anchor"] == 1


def test_live_dry_run_blocks_when_latest_readiness_points_to_missing_artifact(tmp_path) -> None:
    _write_current_files(tmp_path)
    _write_refit_handoff(tmp_path)
    _create_forecast_db_with_replacement_inventory(tmp_path / "state" / "zeus-forecasts.db")
    with sqlite3.connect(tmp_path / "state" / "zeus-forecasts.db") as conn:
        dependency_json = json.loads(
            conn.execute("SELECT dependency_json FROM readiness_state").fetchone()[0]
        )
        dependency_json["dependencies"][0]["artifact_id"] = 999
        conn.execute(
            "UPDATE readiness_state SET dependency_json = ?",
            (json.dumps(dependency_json),),
        )
        conn.commit()
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    report = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(root=tmp_path, runtime_flags=_flags(), optional_dependencies=("requests",))
    )

    assert report.ok is False
    assert report.raw_artifact_lineage_status == "READY"
    assert report.latest_readiness_artifact_status == "MISSING_DEPENDENCY_ARTIFACT_ROW"
    assert "REPLACEMENT_DRY_RUN_LATEST_READINESS_ARTIFACTS_NOT_READY" in report.reason_codes


def test_live_dry_run_ready_after_targeted_shadow_schema_initialization(tmp_path) -> None:
    _write_current_files(tmp_path)
    _write_refit_handoff(tmp_path)
    replacement_tables = set(REPLACEMENT_SHADOW_TABLES)
    _create_db(
        tmp_path / "state" / "zeus-forecasts.db",
        tuple(table for table in REQUIRED_FORECAST_TABLES if table not in replacement_tables),
    )
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    init_report = initialize_replacement_forecast_shadow_schema(tmp_path / "state" / "zeus-forecasts.db", commit=True)
    _insert_raw_artifact_lineage(tmp_path / "state" / "zeus-forecasts.db")
    report = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(root=tmp_path, runtime_flags=_flags(), optional_dependencies=("requests",))
    )

    assert init_report["status"] == "READY"
    assert init_report["missing_live_switch_forecast_tables_after"] == []
    assert report.ok is True


def test_live_dry_run_preview_can_assume_schema_flags_and_current_facts_without_writes(tmp_path) -> None:
    _write_current_files(tmp_path)
    (tmp_path / "docs" / "operations" / "current_source_validity.md").write_text("Status: STALE_FOR_LIVE\n", encoding="utf-8")
    (tmp_path / "docs" / "operations" / "current_data_state.md").write_text("Status: STALE_FOR_LIVE\n", encoding="utf-8")
    replacement_tables = set(REPLACEMENT_SHADOW_TABLES)
    forecast_db = tmp_path / "state" / "zeus-forecasts.db"
    _create_db(
        forecast_db,
        tuple(table for table in REQUIRED_FORECAST_TABLES if table not in replacement_tables),
    )
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    blocked = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(
            root=tmp_path,
            runtime_flags=_flags(shadow=False, veto=False),
            optional_dependencies=("requests",),
        )
    )
    preview = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(
            root=tmp_path,
            runtime_flags=_flags(),
            optional_dependencies=("requests",),
            source_fact_status_override="CURRENT_FOR_LIVE",
            data_fact_status_override="CURRENT_FOR_LIVE",
            assume_replacement_shadow_schema_initialized=True,
            assume_refit_handoff_available=True,
            assume_raw_artifact_lineage_available=True,
        )
    )

    assert blocked.ok is False
    assert "REPLACEMENT_SWITCH_POLICY_NOT_READABLE" in blocked.reason_codes
    assert "REPLACEMENT_SWITCH_SOURCE_FACTS_STALE" in blocked.reason_codes
    assert "REPLACEMENT_SWITCH_MISSING_READ_TABLES" in blocked.reason_codes
    assert preview.ok is True
    assert preview.assumptions["assume_replacement_shadow_schema_initialized"] is True
    assert preview.assumptions["assume_refit_handoff_available"] is True
    assert preview.assumptions["assume_raw_artifact_lineage_available"] is True
    assert preview.refit_handoff_status == "ASSUMED_READY"
    assert preview.raw_artifact_lineage_status == "ASSUMED_READY"
    assert set(preview.assumptions["actual_missing_forecast_tables"]) == replacement_tables
    with sqlite3.connect(forecast_db) as conn:
        rows = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert replacement_tables.isdisjoint(rows)


def test_live_dry_run_cli_reads_settings_from_root_not_imported_worktree_config(tmp_path) -> None:
    _write_current_files(tmp_path)
    _write_refit_handoff(tmp_path)
    _write_settings_flags(tmp_path, shadow=True, veto=True)
    _create_db(tmp_path / "state" / "zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_replacement_forecast_live_dry_run.py",
            "--root",
            str(tmp_path),
            "--stdout",
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode in {0, 1}, result.stderr
    payload = __import__("json").loads(result.stdout)
    assert payload["runtime_policy_status"] == "SHADOW_VETO_ONLY"
    assert payload["live_switch"]["simple_switch_ready"] is True
    assert payload["refit_handoff_status"] == "READY"
    assert "REPLACEMENT_SWITCH_POLICY_NOT_READABLE" not in payload["reason_codes"]


def test_live_dry_run_blocks_live_authority_when_current_market_targets_lack_replacement_coverage(tmp_path) -> None:
    _write_current_files(tmp_path)
    _write_refit_handoff(tmp_path)
    _create_forecast_db_with_replacement_inventory(tmp_path / "state" / "zeus-forecasts.db")
    with sqlite3.connect(tmp_path / "state" / "zeus-forecasts.db") as conn:
        conn.execute("DROP TABLE IF EXISTS market_events")
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
            INSERT INTO market_events (
                market_slug, city, target_date, temperature_metric,
                condition_id, token_id, range_label
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "highest-temperature-in-madrid-on-june-9-2026",
                "Madrid",
                "2026-06-09",
                "high",
                "condition",
                "token",
                "Will the highest temperature in Madrid be 37°C on June 9?",
            ),
        )
        conn.execute(
            """
            INSERT INTO source_run_coverage (
                coverage_id, source_run_id, source_id, city, target_local_date,
                temperature_metric, data_version, completeness_status,
                readiness_status, computed_at, recorded_at
            ) VALUES (
                'coverage-madrid', 'baseline-current-madrid',
                'ecmwf_open_data', 'Madrid', '2026-06-09', 'high',
                'ecmwf_opendata_mx2t3_local_calendar_day_max',
                'COMPLETE', 'LIVE_ELIGIBLE',
                '2026-06-07T08:00:00+00:00',
                '2026-06-07T08:00:00+00:00'
            )
            """
        )
        conn.commit()
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    report = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(
            root=tmp_path,
            runtime_flags=_flags(),
            optional_dependencies=("requests",),
            assume_raw_artifact_lineage_available=True,
        )
    )

    assert report.ok is False
    assert report.current_target_coverage_status == "MISSING_REPLACEMENT_TARGET_COVERAGE"
    assert report.current_target_coverage_counts["target_count"] == 1
    assert report.current_target_coverage_counts["missing_posterior_count"] == 1
    assert report.current_target_coverage_missing_examples[0]["city"] == "Madrid"
    assert "REPLACEMENT_DRY_RUN_CURRENT_TARGET_COVERAGE_NOT_READY" in report.reason_codes


def test_current_target_dry_run_uses_plan_day0_flag_not_utc_today(monkeypatch, tmp_path) -> None:
    """Dry-run future/day0 buckets must consume the city-local planner decision."""

    plan = SimpleNamespace(
        status="CURRENT_TARGETS_REQUIRE_DAY0_OBSERVED_EXTREME",
        rows=(
            SimpleNamespace(
                city="Tokyo",
                target_date="2026-06-08",
                temperature_metric="low",
                posterior_count=0,
                readiness_count=0,
                baseline_source_run_id="baseline-tokyo",
                market_bin_count=11,
                day0_observed_extreme_required=True,
            ),
            SimpleNamespace(
                city="San Francisco",
                target_date="2026-06-08",
                temperature_metric="high",
                posterior_count=0,
                readiness_count=0,
                baseline_source_run_id="baseline-sf",
                market_bin_count=11,
                day0_observed_extreme_required=False,
            ),
        ),
    )

    import src.data.replacement_forecast_current_target_plan as plan_module

    monkeypatch.setattr(
        plan_module,
        "build_replacement_forecast_current_target_plan",
        lambda *args, **kwargs: plan,
    )
    forecast_db = tmp_path / "zeus-forecasts.db"
    forecast_db.touch()

    status, counts, missing = _current_target_coverage_inventory(forecast_db)

    assert status == "MISSING_REPLACEMENT_FUTURE_TARGET_COVERAGE"
    assert counts["day0_or_past_target_count"] == 1
    assert counts["day0_or_past_missing_posterior_count"] == 1
    assert counts["future_target_count"] == 1
    assert counts["future_missing_posterior_count"] == 1
    assert missing == (
        {
            "city": "San Francisco",
            "target_date": "2026-06-08",
            "temperature_metric": "high",
            "baseline_source_run_id": "baseline-sf",
            "market_bin_count": 11,
            "posterior_count": 0,
            "readiness_count": 0,
            "day0_observed_extreme_required": False,
        },
    )
