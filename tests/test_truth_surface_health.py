# Created: 2026-04-07
# Last reused/audited: 2026-04-25
# Authority basis: Venus sensing audit findings; P1 obs_v2 provenance identity packet.
"""Truth-surface health tests — antibodies for Venus sensing audit findings.

These tests verify cross-surface invariants that Venus detected as broken.
They run against the live Zeus database, not test fixtures, because the
invariants they check are about production state integrity.
"""
# Lifecycle: created=2026-04-07; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Protect canonical truth surfaces and P0 training-readiness fail-closed checks.
# Reuse: Inspect high-sensitivity skip metadata and live-DB assumptions before treating full-file results as closeout evidence.

import re
import sqlite3
from datetime import date, datetime, timezone

import pytest

from src.state.db import (
    get_connection,
    get_trade_connection,
    init_schema,
    query_portfolio_loader_view,
)
from src.state.schema.v2_schema import apply_v2_schema
from scripts import verify_truth_surfaces as truth_surfaces
from scripts.verify_truth_surfaces import (
    build_calibration_pair_rebuild_preflight_report,
    build_p4_readiness_report,
    build_platt_refit_preflight_report,
    build_training_readiness_report,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _fresh_training_readiness_world_db(tmp_path):
    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    apply_v2_schema(conn)
    conn.commit()
    conn.close()
    return db_path


def _blocker_codes(report):
    return {item["code"] for item in report["blockers"]}


def _ready_p4_state_dir(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "scheduler_jobs_health.json").write_text(
        """
        {
          "k2_daily_obs": {"status": "OK", "last_success_at": "2026-04-25T12:00:00Z"},
          "k2_forecasts_daily": {"status": "OK", "last_success_at": "2026-04-25T13:00:00Z"}
        }
        """,
        encoding="utf-8",
    )
    (state_dir / "status_summary.json").write_text(
        '{"risk":{"infrastructure_level":"GREEN"}}',
        encoding="utf-8",
    )
    (state_dir / "k2_forecasts_daily_row_count.json").write_text(
        '{"status":"OK","row_count":12}',
        encoding="utf-8",
    )
    return state_dir


def _write_accepted_market_rule(path):
    path.write_text(
        """
        {
          "source_url": "https://polymarket.example/rules/nyc-high",
          "station_id": "KNYC",
          "finalization_policy": "WU integer display after source finalization",
          "rule_version": "market-rule-v1",
          "temperature_metric": "high",
          "unit": "F",
          "bin_identity": {"market_slug": "market-slug", "range_label": "70-71F"}
        }
        """,
        encoding="utf-8",
    )


def _write_accepted_tigge_manifest(path, *, track):
    path.write_text(
        f"""
        {{
          "track": "{track}",
          "cloud_local_parity_verified": true,
          "manifest_hash": "sha256:test-{track}",
          "issue_time_verified": true,
          "available_at_verified": true,
          "files": [{{"name": "sample.json", "sha256": "sha256:file-{track}"}}]
        }}
        """,
        encoding="utf-8",
    )


def _seed_minimal_ready_training_tables(conn, *, seed_observations=True):
    conn.execute("CREATE TABLE forecasts (id INTEGER, retrieved_at TEXT)")
    conn.execute("INSERT INTO forecasts (id, retrieved_at) VALUES (1, '2026-04-22T12:00:00Z')")
    conn.execute(
        """
        CREATE TABLE calibration_pairs_v2 (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            observation_field TEXT,
            range_label TEXT,
            p_raw REAL,
            outcome INTEGER,
            lead_days REAL,
            season TEXT,
            cluster TEXT,
            forecast_available_at TEXT,
            settlement_value REAL,
            decision_group_id TEXT,
            authority TEXT,
            bin_source TEXT,
            data_version TEXT,
            training_allowed INTEGER,
            causality_status TEXT
        )
        """
    )
    _seed_platt_refit_pairs(
        conn,
        n_groups=truth_surfaces.MIN_PLATT_DECISION_GROUPS,
    )
    conn.execute("CREATE TABLE platt_models_v2 (id INTEGER)")
    conn.execute("INSERT INTO platt_models_v2 (id) VALUES (1)")
    conn.execute(
        """
        CREATE TABLE market_events_v2 (
            market_slug TEXT,
            condition_id TEXT,
            token_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO market_events_v2 (
            market_slug, condition_id, token_id, city, target_date, temperature_metric
        ) VALUES (
            'market-slug', 'condition-id', 'token-id',
            'NYC', '2026-04-23', 'high'
        )
        """
    )
    conn.execute(
        "CREATE TABLE market_price_history (market_slug TEXT, token_id TEXT)"
    )
    conn.execute(
        "INSERT INTO market_price_history (market_slug, token_id) VALUES ('market-slug', 'token-id')"
    )
    conn.execute(
        """
        CREATE TABLE historical_forecasts_v2 (
            data_version TEXT,
            provenance_json TEXT,
            available_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO historical_forecasts_v2 (
            data_version, provenance_json, available_at
        ) VALUES ('source_v1', '{}', '2026-04-22T12:00:00Z')
        """
    )
    conn.execute(
        """
        CREATE TABLE ensemble_snapshots_v2 (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            physical_quantity TEXT,
            observation_field TEXT,
            issue_time TEXT,
            valid_time TEXT,
            available_at TEXT,
            fetch_time TEXT,
            lead_hours REAL,
            members_json TEXT,
            model_version TEXT,
            data_version TEXT,
            training_allowed INTEGER,
            causality_status TEXT,
            authority TEXT,
            provenance_json TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, valid_time, available_at,
            fetch_time, lead_hours, members_json, model_version,
            data_version, training_allowed, causality_status, authority,
            provenance_json
        ) VALUES (
            'NYC', '2026-04-23', ?, ?, ?,
            '2026-04-22T12:00:00Z', '2026-04-23T12:00:00Z',
            '2026-04-22T12:10:00Z', '2026-04-22T12:15:00Z',
            24.0, '[70.0, 71.0, 72.0]', 'tigge', ?,
            1, 'OK', 'VERIFIED', '{"source":"tigge"}'
        )
        """,
        [
            (
                "high",
                "mx2t6_local_calendar_day_max",
                "high_temp",
                "tigge_mx2t6_local_calendar_day_max_v1",
            ),
            (
                "low",
                "mn2t6_local_calendar_day_min",
                "low_temp",
                "tigge_mn2t6_local_calendar_day_min_v1",
            ),
        ],
    )
    conn.execute(
        """
        CREATE TABLE settlements_v2 (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            market_slug TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO settlements_v2 (
            city, target_date, temperature_metric, market_slug
        ) VALUES ('NYC', '2026-04-23', 'high', 'market-slug')
        """
    )
    conn.execute(
        """
        CREATE TABLE observation_instants_v2 (
            authority TEXT,
            data_version TEXT,
            training_allowed INTEGER,
            source_role TEXT,
            causality_status TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO observation_instants_v2 (
            authority, data_version, training_allowed, source_role,
            causality_status
        ) VALUES ('VERIFIED', 'v1.wu-native', 1, 'historical_hourly', 'OK')
        """
    )
    if seed_observations:
        conn.execute("CREATE TABLE observations (authority TEXT, provenance_metadata TEXT)")
        conn.execute(
            """
            INSERT INTO observations (
                authority, provenance_metadata
            ) VALUES ('VERIFIED', '{"source":"wu","payload_hash":"hash"}')
            """
        )


def _seed_safe_observation_instant(conn):
    conn.execute(
        """
        INSERT INTO observation_instants_v2 (
            city, target_date, source, timezone_name, local_timestamp,
            utc_timestamp, utc_offset_minutes, time_basis, temp_unit,
            imported_at, authority, data_version, training_allowed,
            source_role, causality_status, provenance_json
        ) VALUES (
            'NYC', '2026-04-23', 'wu_icao_history', 'America/New_York',
            '2026-04-23T10:00:00-04:00', '2026-04-23T14:00:00Z',
            -240, 'hourly', 'F', '2026-04-23T14:05:00Z',
            'VERIFIED', 'v1.wu-native', 1, 'historical_hourly', 'OK',
            '{"tier":"WU_ICAO","station_id":"KNYC","payload_hash":"sha256:fixture","source_url":"https://api.weather.com/v1/location/KNYC:9:US/observations/historical.json?apiKey=REDACTED","parser_version":"test_truth_surface_health_v1"}'
        )
        """
    )


def _seed_rebuild_preflight_inputs(conn):
    _seed_safe_observation_instant(conn)
    snapshot_rows = [
        (
            "high",
            "mx2t6_local_calendar_day_max",
            "high_temp",
            "tigge_mx2t6_local_calendar_day_max_v1",
        ),
        (
            "low",
            "mn2t6_local_calendar_day_min",
            "low_temp",
            "tigge_mn2t6_local_calendar_day_min_v1",
        ),
    ]
    for metric, physical_quantity, observation_field, data_version in snapshot_rows:
        conn.execute(
            """
            INSERT INTO ensemble_snapshots_v2 (
                city, target_date, temperature_metric, physical_quantity,
                observation_field, issue_time, valid_time, available_at,
                fetch_time, lead_hours, members_json, model_version,
                data_version, training_allowed, causality_status, authority,
                provenance_json
            ) VALUES (
                'NYC', '2026-04-23', ?, ?, ?,
                '2026-04-22T12:00:00Z', '2026-04-23T12:00:00Z',
                '2026-04-22T12:10:00Z', '2026-04-22T12:15:00Z',
                24.0, '[70.0, 71.0, 72.0]', 'tigge',
                ?, 1, 'OK', 'VERIFIED', '{"source":"tigge"}'
            )
            """,
            (metric, physical_quantity, observation_field, data_version),
        )
    conn.execute(
        """
        INSERT INTO observations (
            city, target_date, source, high_temp, low_temp, unit,
            station_id, fetched_at, authority,
            high_provenance_metadata, low_provenance_metadata
        ) VALUES (
            'NYC', '2026-04-23', 'wu_icao_history', 72.0, 61.0, 'F',
            'KNYC', '2026-04-23T23:55:00Z', 'VERIFIED',
            '{"source":"wu","payload_hash":"high"}',
            '{"source":"wu","payload_hash":"low"}'
        )
        """
    )


def _seed_platt_refit_pairs(conn, *, n_groups=None):
    if n_groups is None:
        n_groups = truth_surfaces.MIN_PLATT_DECISION_GROUPS
    pair_specs = [
        (
            "high",
            "high_temp",
            "tigge_mx2t6_local_calendar_day_max_v1",
        ),
        (
            "low",
            "low_temp",
            "tigge_mn2t6_local_calendar_day_min_v1",
        ),
    ]
    for metric, observation_field, data_version in pair_specs:
        for index in range(n_groups):
            conn.execute(
                """
                INSERT INTO calibration_pairs_v2 (
                    city, target_date, temperature_metric, observation_field,
                    range_label, p_raw, outcome, lead_days, season, cluster,
                    forecast_available_at, settlement_value, decision_group_id,
                    authority, bin_source, data_version, training_allowed,
                    causality_status
                ) VALUES (
                    'NYC', ?, ?, ?, '70-71F', ?, ?, 1.0, 'spring',
                    'temperate', '2026-04-22T12:10:00Z', 71.0, ?,
                    'VERIFIED', 'canonical_v2', ?, 1, 'OK'
                )
                """,
                (
                    f"2026-04-{index + 1:02d}",
                    metric,
                    observation_field,
                    0.25 + (index / 100.0),
                    index % 2,
                    f"{metric}-decision-group-{index}",
                    data_version,
                ),
            )


def _create_legacy_settlements_table(conn):
    columns = [
        "id INTEGER PRIMARY KEY",
        "city TEXT",
        "target_date TEXT",
        "market_slug TEXT",
        "winning_bin TEXT",
        "settlement_value REAL",
        "settlement_source TEXT",
        "settlement_source_type TEXT",
        "settled_at TEXT",
        "authority TEXT",
        "unit TEXT",
        "temperature_metric TEXT",
        "provenance_json TEXT",
    ]
    conn.execute(
        f"""
        CREATE TABLE settlements (
            {", ".join(columns)}
        )
        """
    )


def _add_legacy_settlement_alias_columns(conn):
    for column in (
        "source_url",
        "source_page",
        "finalized_at",
        "finalization_policy",
        "revision_policy",
        "late_update_policy",
        "market_rule_version",
        "settlement_rule_version",
        "oracle_transform",
    ):
        conn.execute(f"ALTER TABLE settlements ADD COLUMN {column} TEXT")


def _insert_legacy_settlement(
    conn,
    *,
    market_slug="market-slug",
    settlement_value=72.0,
    winning_bin="72F",
    unit="F",
    temperature_metric="high",
    provenance_json='{"source":"wu","payload_hash":"hash","rounding_rule":"wmo_half_up"}',
    settlement_source="https://www.wunderground.com/history/daily/us/ny/new-york/KNYC",
    settlement_source_type="WU",
    settled_at="2026-04-23T19:50:34+00:00",
    authority="VERIFIED",
):
    columns = [row[1] for row in conn.execute("PRAGMA table_info(settlements)").fetchall()]
    values = {
        "city": "NYC",
        "target_date": "2026-04-23",
        "market_slug": market_slug,
        "winning_bin": winning_bin,
        "settlement_value": settlement_value,
        "settlement_source": settlement_source,
        "settlement_source_type": settlement_source_type,
        "settled_at": settled_at,
        "authority": authority,
        "unit": unit,
        "temperature_metric": temperature_metric,
        "provenance_json": provenance_json,
    }
    insert_columns = [column for column in values if column in columns]
    placeholders = ", ".join("?" for _ in insert_columns)
    conn.execute(
        f"""
        INSERT INTO settlements ({", ".join(insert_columns)})
        VALUES ({placeholders})
        """,
        [values[column] for column in insert_columns],
    )


class TestTrainingReadinessP0:
    """P0: forensic data-readiness checks are read-only and fail closed."""

    def test_training_readiness_opens_world_db_read_only(self, tmp_path, monkeypatch):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        calls = []
        real_connect = truth_surfaces.sqlite3.connect

        def capture_connect(database, *args, **kwargs):
            calls.append((database, kwargs))
            return real_connect(database, *args, **kwargs)

        monkeypatch.setattr(truth_surfaces.sqlite3, "connect", capture_connect)

        build_training_readiness_report(db_path)

        assert calls
        database, kwargs = calls[0]
        assert str(database).startswith(f"file:{db_path}")
        assert "mode=ro" in str(database)
        assert kwargs.get("uri") is True

    def test_training_readiness_fails_when_required_v2_tables_are_empty(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert report["status"] == "NOT_READY"
        checks = report["checks"]
        for table in [
            "forecasts",
            "historical_forecasts_v2",
            "ensemble_snapshots_v2",
            "calibration_pairs_v2",
            "platt_models_v2",
            "market_events_v2",
            "market_price_history",
            "settlements_v2",
            "observation_instants_v2",
            "observations",
        ]:
            assert checks[table]["status"] == "FAIL"
            assert checks[table]["count"] == 0

    def test_training_readiness_requires_per_metric_eligible_snapshots(self, tmp_path):
        db_path = tmp_path / "missing-high-snapshot-eligibility-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute(
            """
            UPDATE ensemble_snapshots_v2
            SET training_allowed = 0
            WHERE temperature_metric = 'high'
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "empty_rebuild_eligible_snapshots" in _blocker_codes(report)
        high_check = report["checks"]["ensemble_snapshots_v2.high.rebuild_eligible_present"]
        low_check = report["checks"]["ensemble_snapshots_v2.low.rebuild_eligible_present"]
        assert high_check["status"] == "FAIL"
        assert high_check["count"] == 0
        assert low_check["status"] == "PASS"

    def test_training_readiness_requires_per_metric_mature_platt_bucket(self, tmp_path):
        db_path = tmp_path / "immature-calibration-pairs-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute(
            """
            DELETE FROM calibration_pairs_v2
            WHERE temperature_metric = 'high'
              AND target_date = '2026-04-01'
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "empty_platt_refit_bucket" in _blocker_codes(report)
        high_check = report["checks"]["calibration_pairs_v2.high.mature_bucket_present"]
        low_check = report["checks"]["calibration_pairs_v2.low.mature_bucket_present"]
        assert high_check["status"] == "FAIL"
        assert high_check["count"] == 0
        assert low_check["status"] == "PASS"

    def test_rebuild_preflight_does_not_require_target_artifacts(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _seed_rebuild_preflight_inputs(conn)
        conn.commit()
        conn.close()

        report = build_calibration_pair_rebuild_preflight_report(db_path)
        training_report = build_training_readiness_report(db_path)

        assert report["ready"] is True
        assert report["mode"] == "calibration-pair-rebuild-preflight"
        assert "calibration_pairs_v2" not in report["checks"]
        assert "platt_models_v2" not in report["checks"]
        assert training_report["ready"] is False
        assert "empty_v2_table" in _blocker_codes(training_report)

    def test_rebuild_preflight_fails_when_snapshot_identity_is_unsafe(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _seed_rebuild_preflight_inputs(conn)
        conn.execute(
            """
            UPDATE ensemble_snapshots_v2
            SET physical_quantity = '',
                available_at = 'reconstructed_from_target_date'
            WHERE temperature_metric = 'high'
            """
        )
        conn.commit()
        conn.close()

        report = build_calibration_pair_rebuild_preflight_report(db_path)

        assert report["ready"] is False
        assert "ensemble_snapshots_v2.rebuild_input_unsafe" in _blocker_codes(report)
        assert "empty_rebuild_eligible_snapshots" in _blocker_codes(report)

    def test_rebuild_preflight_fails_when_wu_label_provenance_is_empty(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _seed_rebuild_preflight_inputs(conn)
        conn.execute(
            """
            UPDATE observations
            SET high_provenance_metadata = '{}'
            WHERE source = 'wu_icao_history'
            """
        )
        conn.commit()
        conn.close()

        report = build_calibration_pair_rebuild_preflight_report(db_path)

        assert report["ready"] is False
        blockers = _blocker_codes(report)
        assert "observations.verified_without_provenance" in blockers
        assert "observations.wu_empty_provenance" in blockers
        assert report["checks"]["observations.high.wu_provenance_present"]["count"] == 1

    def test_rebuild_preflight_fails_when_observation_instant_is_unsafe(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _seed_rebuild_preflight_inputs(conn)
        conn.execute(
            """
            UPDATE observation_instants_v2
            SET source_role = 'fallback', causality_status = 'UNKNOWN'
            """
        )
        conn.commit()
        conn.close()

        report = build_calibration_pair_rebuild_preflight_report(db_path)

        blockers = _blocker_codes(report)
        assert "observation_instants_v2.training_role_unsafe" in blockers
        assert "observation_instants_v2.causality_unsafe" in blockers

    def test_platt_refit_preflight_does_not_require_existing_platt_models(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _seed_platt_refit_pairs(conn)
        conn.commit()
        conn.close()

        report = build_platt_refit_preflight_report(db_path)

        assert report["ready"] is True
        assert report["mode"] == "platt-refit-preflight"
        assert "platt_models_v2" not in report["checks"]
        assert report["checks"]["calibration_pairs_v2.high.mature_bucket_present"]["status"] == "PASS"
        assert report["checks"]["calibration_pairs_v2.low.mature_bucket_present"]["status"] == "PASS"

    def test_platt_refit_preflight_fails_when_pair_inputs_are_unsafe(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _seed_platt_refit_pairs(conn)
        conn.execute(
            """
            UPDATE calibration_pairs_v2
            SET p_raw = 1.2, causality_status = 'UNKNOWN', decision_group_id = ''
            WHERE temperature_metric = 'high'
              AND target_date = '2026-04-01'
            """
        )
        conn.commit()
        conn.close()

        report = build_platt_refit_preflight_report(db_path)

        blockers = _blocker_codes(report)
        assert "calibration_pairs_v2.p_raw_domain_unsafe" in blockers
        assert "calibration_pairs_v2.causality_unsafe" in blockers
        assert "calibration_pairs_v2.decision_group_missing" in blockers

    def test_rebuild_live_write_refuses_when_preflight_is_not_ready(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        from scripts import rebuild_calibration_pairs_v2

        db_path = _fresh_training_readiness_world_db(tmp_path)

        monkeypatch.setattr(
            rebuild_calibration_pairs_v2.sys,
            "argv",
            [
                "rebuild_calibration_pairs_v2.py",
                "--no-dry-run",
                "--force",
                "--db",
                str(db_path),
            ],
        )

        assert rebuild_calibration_pairs_v2.main() == 1
        assert "preflight is NOT_READY" in capsys.readouterr().err

    def test_refit_live_write_refuses_when_preflight_is_not_ready(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        from scripts import refit_platt_v2

        db_path = _fresh_training_readiness_world_db(tmp_path)
        monkeypatch.setattr(
            refit_platt_v2.sys,
            "argv",
            [
                "refit_platt_v2.py",
                "--no-dry-run",
                "--force",
                "--db",
                str(db_path),
            ],
        )

        assert refit_platt_v2.main() == 1
        assert "preflight is NOT_READY" in capsys.readouterr().err

    def test_training_readiness_fails_when_required_truth_surfaces_are_empty(self, tmp_path):
        db_path = tmp_path / "sparse-world.db"
        conn = sqlite3.connect(db_path)
        for table in [
            "forecasts",
            "calibration_pairs_v2",
            "platt_models_v2",
            "market_events_v2",
            "market_price_history",
        ]:
            conn.execute(f"CREATE TABLE {table} (id INTEGER)")
            conn.execute(f"INSERT INTO {table} (id) VALUES (1)")
        conn.execute(
            """
            CREATE TABLE historical_forecasts_v2 (
                data_version TEXT,
                provenance_json TEXT,
                available_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO historical_forecasts_v2 (
                data_version, provenance_json, available_at
            ) VALUES ('source_v1', '{}', '2026-04-22T12:00:00Z')
            """
        )
        conn.execute(
            """
            CREATE TABLE ensemble_snapshots_v2 (
                issue_time TEXT,
                available_at TEXT,
                fetch_time TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO ensemble_snapshots_v2 (
                issue_time, available_at, fetch_time
            ) VALUES (
                '2026-04-22T12:00:00Z',
                '2026-04-22T12:00:00Z',
                '2026-04-22T12:05:00Z'
            )
            """
        )
        conn.execute("CREATE TABLE settlements_v2 (market_slug TEXT)")
        conn.execute("INSERT INTO settlements_v2 (market_slug) VALUES ('market-slug')")
        conn.execute(
            "CREATE TABLE observation_instants_v2 (training_allowed INTEGER, source_role TEXT)"
        )
        conn.execute("CREATE TABLE observations (authority TEXT, provenance_metadata TEXT)")
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert report["checks"]["observation_instants_v2"]["status"] == "FAIL"
        assert report["checks"]["observations"]["status"] == "FAIL"
        blockers = {(item["code"], item["table"]) for item in report["blockers"]}
        assert ("empty_required_table", "observation_instants_v2") in blockers
        assert ("empty_required_table", "observations") in blockers

    def test_training_readiness_fails_when_observations_lack_provenance_columns(self, tmp_path):
        db_path = tmp_path / "no-provenance-columns-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=False)
        conn.execute("CREATE TABLE observations (authority TEXT)")
        conn.execute("INSERT INTO observations (authority) VALUES ('UNVERIFIED')")
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "missing_observation_provenance_columns" in _blocker_codes(report)
        check = report["checks"]["observations.provenance_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 0

    def test_training_readiness_fails_when_no_training_eligible_observation_instants_exist(self, tmp_path):
        db_path = tmp_path / "no-training-eligible-observations-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute("DELETE FROM observation_instants_v2")
        conn.execute(
            "INSERT INTO observation_instants_v2 (training_allowed, source_role) VALUES (0, 'historical_hourly')"
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "empty_training_eligible_observations" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.training_eligible_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 0

    def test_training_readiness_fails_when_source_role_is_unknown(self, tmp_path):
        db_path = tmp_path / "unknown-source-role-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute("DELETE FROM observation_instants_v2")
        conn.execute(
            """
            INSERT INTO observation_instants_v2 (
                training_allowed, source_role, causality_status
            ) VALUES (1, 'banana', 'OK')
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "empty_training_eligible_observations" in _blocker_codes(report)
        assert "observation_instants_v2.training_role_unsafe" in _blocker_codes(report)
        assert report["checks"]["observation_instants_v2.training_role_unsafe"]["count"] == 1

    def test_training_readiness_fails_when_source_role_is_settlement_truth(self, tmp_path):
        db_path = tmp_path / "settlement-truth-source-role-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute("DELETE FROM observation_instants_v2")
        conn.execute(
            """
            INSERT INTO observation_instants_v2 (
                training_allowed, source_role, causality_status
            ) VALUES (1, 'settlement_truth', 'OK')
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "empty_training_eligible_observations" in _blocker_codes(report)
        assert "observation_instants_v2.training_role_unsafe" in _blocker_codes(report)
        assert report["checks"]["observation_instants_v2.training_role_unsafe"]["count"] == 1

    def test_training_readiness_fails_when_observations_have_no_verified_rows(self, tmp_path):
        db_path = tmp_path / "no-verified-observations-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=False)
        conn.execute("CREATE TABLE observations (authority TEXT, provenance_metadata TEXT)")
        conn.execute(
            "INSERT INTO observations (authority, provenance_metadata) VALUES ('UNVERIFIED', '{}')"
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "empty_verified_observations" in _blocker_codes(report)
        check = report["checks"]["observations.verified_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 0

    def test_training_readiness_fails_when_split_observation_provenance_is_partial(self, tmp_path):
        db_path = tmp_path / "partial-split-provenance-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=False)
        conn.execute(
            """
            CREATE TABLE observations (
                authority TEXT,
                high_provenance_metadata TEXT,
                low_provenance_metadata TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO observations (
                authority, high_provenance_metadata, low_provenance_metadata
            ) VALUES ('VERIFIED', '', '{"source":"wu"}')
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "observations.verified_without_provenance" in _blocker_codes(report)
        check = report["checks"]["observations.provenance_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    @pytest.mark.parametrize("provenance", ["{}", "[]", " { } ", " [ ] "])
    def test_training_readiness_fails_when_verified_provenance_is_empty_json(
        self, tmp_path, provenance
    ):
        db_path = tmp_path / "empty-json-provenance-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=False)
        conn.execute(
            """
            CREATE TABLE observations (
                source TEXT,
                authority TEXT,
                provenance_metadata TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO observations (
                source, authority, provenance_metadata
            ) VALUES ('ogimet_metar_llbg', 'VERIFIED', ?)
            """,
            (provenance,),
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "observations.verified_without_provenance" in _blocker_codes(report)
        check = report["checks"]["observations.provenance_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_reports_wu_empty_provenance_separately(self, tmp_path):
        db_path = tmp_path / "wu-empty-provenance-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=False)
        conn.execute(
            """
            CREATE TABLE observations (
                source TEXT,
                authority TEXT,
                provenance_metadata TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO observations (
                source, authority, provenance_metadata
            ) VALUES (?, 'VERIFIED', ?)
            """,
            [
                ("wu_icao_history", "{}"),
                ("ogimet_metar_llbg", "{}"),
                ("hko_daily_api", '{"source":"hko","payload_hash":"hash"}'),
            ],
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        blockers = _blocker_codes(report)
        assert "observations.verified_without_provenance" in blockers
        assert "observations.wu_empty_provenance" in blockers
        assert report["checks"]["observations.provenance_present"]["count"] == 2
        assert report["checks"]["observations.wu_provenance_present"]["count"] == 1

    def test_training_readiness_fails_when_required_eligibility_tables_are_missing(self, tmp_path):
        db_path = tmp_path / "raw-world.db"
        sqlite3.connect(db_path).close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        checks = report["checks"]
        for check_id in [
            "observation_instants_v2.training_role_unsafe",
            "observation_instants_v2.causality_unsafe",
            "historical_forecasts_v2.available_at_not_reconstructed",
            "observations.provenance_present",
        ]:
            assert checks[check_id]["status"] == "FAIL"
        blockers = {(item["code"], item["table"]) for item in report["blockers"]}
        assert ("missing_table", "observation_instants_v2") in blockers
        assert ("missing_table", "historical_forecasts_v2") in blockers
        assert ("missing_table", "observations") in blockers

    def test_training_readiness_fails_when_settlements_v2_market_slug_is_null(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO settlements_v2 (
                city, target_date, temperature_metric, market_slug, authority
            ) VALUES ('NYC', '2026-04-23', 'high', NULL, 'VERIFIED')
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "null_market_slug" in _blocker_codes(report)
        check = report["checks"]["settlements_v2.market_identity_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_ignores_absent_legacy_settlements_table(self, tmp_path):
        db_path = tmp_path / "no-legacy-settlements-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is True
        assert not {
            "settlements.legacy_market_identity_missing",
            "settlements.legacy_finalization_policy_missing",
            "settlements.legacy_value_incomplete",
            "settlements.legacy_evidence_only",
        } & _blocker_codes(report)
        assert report["checks"]["settlements.legacy_evidence_only"]["status"] == "PASS"

    def test_training_readiness_fails_when_legacy_settlements_lack_market_identity(self, tmp_path):
        db_path = tmp_path / "legacy-missing-market-identity-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        _create_legacy_settlements_table(conn)
        _insert_legacy_settlement(conn, market_slug=None)
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "settlements.legacy_market_identity_missing" in _blocker_codes(report)
        check = report["checks"]["settlements.legacy_market_identity_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_fails_when_legacy_finalization_contract_is_absent(self, tmp_path):
        db_path = tmp_path / "legacy-missing-finalization-contract-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        _create_legacy_settlements_table(conn)
        _insert_legacy_settlement(conn)
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "settlements.legacy_finalization_policy_missing" in _blocker_codes(report)
        check = report["checks"]["settlements.legacy_finalization_policy_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_rejects_ad_hoc_legacy_finalization_aliases(self, tmp_path):
        db_path = tmp_path / "legacy-ad-hoc-finalization-alias-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        _create_legacy_settlements_table(conn)
        _add_legacy_settlement_alias_columns(conn)
        _insert_legacy_settlement(conn)
        conn.execute(
            """
            UPDATE settlements
            SET source_url = settlement_source,
                source_page = settlement_source,
                finalized_at = settled_at,
                finalization_policy = 'synthetic-policy',
                revision_policy = 'synthetic-revision-policy',
                late_update_policy = 'synthetic-late-update-policy',
                market_rule_version = 'synthetic-market-rule-v1',
                settlement_rule_version = 'synthetic-settlement-rule-v1',
                oracle_transform = 'synthetic-transform'
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        blockers = _blocker_codes(report)
        assert "settlements.legacy_finalization_policy_missing" in blockers
        check = report["checks"]["settlements.legacy_finalization_policy_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_fails_when_legacy_value_evidence_is_incomplete(self, tmp_path):
        db_path = tmp_path / "legacy-incomplete-value-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        _create_legacy_settlements_table(conn)
        _insert_legacy_settlement(
            conn,
            settlement_value=None,
            winning_bin="",
            unit=None,
            provenance_json="{}",
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "settlements.legacy_value_incomplete" in _blocker_codes(report)
        check = report["checks"]["settlements.legacy_value_complete"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_does_not_count_quarantined_legacy_value_gaps(self, tmp_path):
        db_path = tmp_path / "legacy-quarantined-value-gap-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        _create_legacy_settlements_table(conn)
        _insert_legacy_settlement(
            conn,
            authority="QUARANTINED",
            settlement_value=None,
            winning_bin=None,
            unit=None,
            provenance_json='{"source":"wu","rounding_rule":"wmo_half_up"}',
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "settlements.legacy_value_incomplete" not in _blocker_codes(report)
        check = report["checks"]["settlements.legacy_value_complete"]
        assert check["status"] == "PASS"
        assert check["count"] == 0

    def test_training_readiness_reports_legacy_evidence_only_when_v2_is_empty(self, tmp_path):
        db_path = tmp_path / "legacy-evidence-only-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute("DELETE FROM settlements_v2")
        _create_legacy_settlements_table(conn)
        _insert_legacy_settlement(conn)
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "settlements.legacy_evidence_only" in _blocker_codes(report)
        check = report["checks"]["settlements.legacy_evidence_only"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_fails_closed_on_legacy_finalization_even_when_v2_identity_is_ready(self, tmp_path):
        db_path = tmp_path / "legacy-complete-evidence-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        _create_legacy_settlements_table(conn)
        _insert_legacy_settlement(conn)
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        blockers = _blocker_codes(report)
        assert report["ready"] is False
        assert "settlements.legacy_finalization_policy_missing" in blockers
        assert "settlements.legacy_market_identity_missing" not in blockers
        assert "settlements.legacy_value_incomplete" not in blockers
        assert "settlements.legacy_evidence_only" not in blockers
        assert report["checks"]["settlements.legacy_finalization_policy_present"]["count"] == 1

    def test_training_readiness_fails_when_market_identity_columns_are_missing(self, tmp_path):
        db_path = tmp_path / "missing-market-identity-columns-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute("DROP TABLE market_events_v2")
        conn.execute("CREATE TABLE market_events_v2 (id INTEGER)")
        conn.execute("INSERT INTO market_events_v2 (id) VALUES (1)")
        conn.execute("DROP TABLE market_price_history")
        conn.execute("CREATE TABLE market_price_history (id INTEGER)")
        conn.execute("INSERT INTO market_price_history (id) VALUES (1)")
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "missing_market_identity_columns" in _blocker_codes(report)
        assert report["checks"]["market_events_v2.market_identity_present"]["status"] == "FAIL"
        assert report["checks"]["market_price_history.market_identity_present"]["status"] == "FAIL"

    def test_training_readiness_fails_when_market_identity_values_are_empty(self, tmp_path):
        db_path = tmp_path / "empty-market-identity-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute("UPDATE market_events_v2 SET condition_id=''")
        conn.execute("UPDATE market_price_history SET token_id=NULL")
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "missing_market_identity" in _blocker_codes(report)
        assert report["checks"]["market_events_v2.market_identity_present"]["count"] == 1
        assert report["checks"]["market_price_history.market_identity_present"]["count"] == 1

    def test_training_readiness_fails_when_observation_instants_v2_source_role_is_fallback(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO observation_instants_v2 (
                city, target_date, source, timezone_name, local_timestamp,
                utc_timestamp, utc_offset_minutes, time_basis, temp_unit,
                imported_at, training_allowed, source_role
            ) VALUES (
                'NYC', '2026-04-23', 'openmeteo', 'America/New_York',
                '2026-04-23T10:00:00-04:00', '2026-04-23T14:00:00Z',
                -240, 'hourly', 'F', '2026-04-23T14:05:00Z', 1, 'fallback'
            )
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "observation_instants_v2.training_role_unsafe" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.training_role_unsafe"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_fails_when_observation_instants_v2_source_role_is_null(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO observation_instants_v2 (
                city, target_date, source, timezone_name, local_timestamp,
                utc_timestamp, utc_offset_minutes, time_basis, temp_unit,
                imported_at, training_allowed, source_role
            ) VALUES (
                'NYC', '2026-04-23', 'openmeteo', 'America/New_York',
                '2026-04-23T10:00:00-04:00', '2026-04-23T14:00:00Z',
                -240, 'hourly', 'F', '2026-04-23T14:05:00Z', 1, NULL
            )
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "observation_instants_v2.training_role_unsafe" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.training_role_unsafe"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    @pytest.mark.parametrize("authority", [None, "", "UNVERIFIED", "QUARANTINED"])
    def test_training_readiness_fails_when_observation_instants_v2_authority_is_unsafe(
        self, tmp_path, authority
    ):
        db_path = tmp_path / "unsafe-observation-authority-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute(
            "UPDATE observation_instants_v2 SET authority = ?",
            (authority,),
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "observation_instants_v2.reader_identity_unsafe" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.reader_identity_unsafe"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    @pytest.mark.parametrize("data_version", [None, "", "v0", "wu-native", "v2.future"])
    def test_training_readiness_fails_when_observation_instants_v2_data_version_is_unsafe(
        self, tmp_path, data_version
    ):
        db_path = tmp_path / "unsafe-observation-data-version-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute(
            "UPDATE observation_instants_v2 SET data_version = ?",
            (data_version,),
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "observation_instants_v2.reader_identity_unsafe" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.reader_identity_unsafe"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_fails_when_observation_instants_v2_lacks_source_role_columns(self, tmp_path):
        db_path = tmp_path / "legacy-world.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE observation_instants_v2 (city TEXT)")
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "missing_source_role_columns" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.training_role_unsafe"]
        assert check["status"] == "FAIL"

    @pytest.mark.parametrize("causality_status", [None, "", "STALE", "N/A_CAUSAL_DAY_STARTED"])
    def test_training_readiness_fails_when_causality_status_is_unsafe(
        self, tmp_path, causality_status
    ):
        db_path = tmp_path / "unsafe-causality-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute("DELETE FROM observation_instants_v2")
        conn.execute(
            """
            INSERT INTO observation_instants_v2 (
                training_allowed, source_role, causality_status
            ) VALUES (1, 'historical_hourly', ?)
            """,
            (causality_status,),
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "observation_instants_v2.causality_unsafe" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.causality_unsafe"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_fails_when_causality_status_column_is_missing(self, tmp_path):
        db_path = tmp_path / "missing-causality-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute("DROP TABLE observation_instants_v2")
        conn.execute(
            """
            CREATE TABLE observation_instants_v2 (
                training_allowed INTEGER,
                source_role TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO observation_instants_v2 (
                training_allowed, source_role
            ) VALUES (1, 'historical_hourly')
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "observation_instants_v2.causality_unsafe" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.causality_unsafe"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_passes_payload_identity_when_contract_is_absent(self, tmp_path):
        db_path = tmp_path / "no-payload-contract-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is True
        assert "payload_identity_missing" not in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.payload_identity_present"]
        assert check["status"] == "PASS"
        assert check["count"] == 0

    def test_training_readiness_fails_when_provenance_json_payload_identity_is_incomplete(
        self, tmp_path
    ):
        db_path = tmp_path / "incomplete-json-payload-contract-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute("ALTER TABLE observation_instants_v2 ADD COLUMN provenance_json TEXT")
        conn.execute(
            """
            UPDATE observation_instants_v2
            SET provenance_json = '{"tier":"WU_ICAO","station_id":"KORD","payload_hash":"sha256:x"}'
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "payload_identity_missing" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.payload_identity_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_accepts_provenance_json_payload_identity(
        self, tmp_path
    ):
        db_path = tmp_path / "json-payload-contract-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        conn.execute("ALTER TABLE observation_instants_v2 ADD COLUMN provenance_json TEXT")
        conn.execute(
            """
            UPDATE observation_instants_v2
            SET provenance_json = '{
                "tier":"WU_ICAO",
                "station_id":"KORD",
                "payload_hash":"sha256:x",
                "source_url":"https://api.weather.com/redacted",
                "parser_version":"parser-v1"
            }'
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is True
        assert "payload_identity_missing" not in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.payload_identity_present"]
        assert check["status"] == "PASS"
        assert check["count"] == 0

    def test_training_readiness_fails_when_payload_identity_contract_is_incomplete(self, tmp_path):
        db_path = tmp_path / "incomplete-payload-contract-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        for column in (
            "payload_hash",
            "parser_version",
            "source_file",
            "station_registry_hash",
        ):
            conn.execute(f"ALTER TABLE observation_instants_v2 ADD COLUMN {column} TEXT")
        conn.execute(
            """
            UPDATE observation_instants_v2
            SET payload_hash = '',
                parser_version = 'parser-v1',
                source_file = 'observations/raw.json',
                station_registry_hash = 'station-registry-v1'
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "payload_identity_missing" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.payload_identity_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_accepts_payload_identity_alternative_columns(self, tmp_path):
        db_path = tmp_path / "payload-alternatives-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=True)
        for column in (
            "payload_hash",
            "parser_version",
            "source_url",
            "source_file",
            "station_registry_version",
            "station_registry_hash",
        ):
            conn.execute(f"ALTER TABLE observation_instants_v2 ADD COLUMN {column} TEXT")
        conn.execute(
            """
            UPDATE observation_instants_v2
            SET payload_hash = 'payload-hash',
                parser_version = 'parser-v1',
                source_url = '',
                source_file = 'observations/raw.json',
                station_registry_version = '',
                station_registry_hash = 'station-registry-hash'
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is True
        assert "payload_identity_missing" not in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.payload_identity_present"]
        assert check["status"] == "PASS"
        assert check["count"] == 0

    @pytest.mark.parametrize("missing_column", ["issue_time", "available_at", "fetch_time"])
    def test_training_readiness_fails_when_ensemble_snapshots_v2_time_is_missing(
        self, tmp_path, missing_column
    ):
        db_path = tmp_path / "legacy-ensemble-world.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE ensemble_snapshots_v2 (
                issue_time TEXT,
                available_at TEXT,
                fetch_time TEXT
            )
            """
        )
        values = {
            "issue_time": "'2026-04-22T12:00:00Z'",
            "available_at": "'2026-04-22T12:00:00Z'",
            "fetch_time": "'2026-04-22T12:05:00Z'",
        }
        values[missing_column] = "NULL"
        conn.execute(
            f"""
            INSERT INTO ensemble_snapshots_v2 (
                issue_time, available_at, fetch_time
            ) VALUES ({values["issue_time"]}, {values["available_at"]}, {values["fetch_time"]})
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "missing_issue_time" in _blocker_codes(report)
        check = report["checks"]["ensemble_snapshots_v2.issue_time_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_fails_when_historical_forecasts_v2_available_at_is_reconstructed(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO historical_forecasts_v2 (
                city, target_date, source, temperature_metric, forecast_value,
                temp_unit, lead_days, available_at, authority, data_version,
                provenance_json
            ) VALUES (
                'NYC', '2026-04-23', 'openmeteo', 'high', 70.0,
                'F', 1, '2026-04-22T12:00:00Z', 'VERIFIED',
                'reconstructed_available_at_v1',
                '{"available_at": "reconstructed"}'
            )
            """
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert "reconstructed_available_at" in _blocker_codes(report)
        check = report["checks"]["historical_forecasts_v2.available_at_not_reconstructed"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_reports_all_blockers_in_json_shape(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        report = build_training_readiness_report(db_path)

        assert set(report) >= {"mode", "database", "status", "ready", "checks", "blockers"}
        assert report["mode"] == "training-readiness"
        assert report["ready"] is False
        assert isinstance(report["checks"], dict)
        assert isinstance(report["blockers"], list)
        assert "empty_v2_table" in _blocker_codes(report)
        for check in report["checks"].values():
            assert set(check) >= {"id", "status", "detail"}


class TestP4Readiness:
    """P4: post-P3 readiness checker is read-only and fail-closed."""

    def test_p4_readiness_opens_world_db_read_only(self, tmp_path, monkeypatch):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        state_dir = _ready_p4_state_dir(tmp_path)
        calls = []
        real_connect = truth_surfaces.sqlite3.connect

        def capture_connect(database, *args, **kwargs):
            calls.append((database, kwargs))
            return real_connect(database, *args, **kwargs)

        monkeypatch.setattr(truth_surfaces.sqlite3, "connect", capture_connect)

        build_p4_readiness_report(
            db_path,
            state_dir=state_dir,
            env={},
            market_rule_paths=(tmp_path / "missing-rules",),
            tigge_track_paths={
                "mx2t6_high": (tmp_path / "missing-mx",),
                "mn2t6_low": (tmp_path / "missing-mn",),
            },
        )

        assert calls
        database, kwargs = calls[0]
        assert str(database).startswith(f"file:{db_path}")
        assert "mode=ro" in str(database)
        assert kwargs.get("uri") is True

    def test_p4_readiness_reports_post_p3_blockers(self, tmp_path):
        db_path = _fresh_training_readiness_world_db(tmp_path)
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        report = build_p4_readiness_report(
            db_path,
            state_dir=state_dir,
            env={},
            market_rule_paths=(tmp_path / "missing-rules",),
            tigge_track_paths={
                "mx2t6_high": (tmp_path / "missing-mx",),
                "mn2t6_low": (tmp_path / "missing-mn",),
            },
        )

        assert report["ready"] is False
        assert report["status"] == "NOT_READY"
        assert report["mode"] == "p4-readiness"
        blockers = _blocker_codes(report)
        assert {
            "p4_metric_layer_decision_missing",
            "p4_market_rule_acceptance_contract_missing",
            "p4_tigge_manifest_missing",
            "p4_market_events_v2_empty",
            "p4_settlements_v2_empty",
            "p4_ensemble_snapshots_v2_empty",
            "p4_calibration_pairs_v2_empty",
            "p4_wu_api_key_missing",
            "p4_scheduler_health_missing",
            "p4_forecast_row_count_evidence_missing",
            "p4_status_summary_unavailable",
        }.issubset(blockers)
        for blocker in report["blockers"]:
            assert blocker["lane"] in report["lanes"]
            assert blocker["code"].startswith("p4_")
        assert report["checks"]["p4.4_8.status_summary_infrastructure_green"]["status"] == "FAIL"

    def test_p4_readiness_can_pass_with_explicit_operator_evidence(self, tmp_path):
        db_path = tmp_path / "p4-ready-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=False)
        conn.commit()
        conn.close()
        state_dir = _ready_p4_state_dir(tmp_path)
        market_rule_file = tmp_path / "market_rule.json"
        _write_accepted_market_rule(market_rule_file)
        mx_dir = tmp_path / "tigge_mx"
        mn_dir = tmp_path / "tigge_mn"
        mx_dir.mkdir()
        mn_dir.mkdir()
        _write_accepted_tigge_manifest(mx_dir / "manifest.json", track="mx2t6_high")
        _write_accepted_tigge_manifest(mn_dir / "manifest.json", track="mn2t6_low")

        report = build_p4_readiness_report(
            db_path,
            state_dir=state_dir,
            env={"WU_API_KEY": "present"},
            market_rule_paths=(market_rule_file,),
            tigge_track_paths={
                "mx2t6_high": (mx_dir,),
                "mn2t6_low": (mn_dir,),
            },
            metric_layer_decision="daily-aggregate",
        )

        assert report["ready"] is True
        assert report["status"] == "READY"
        assert report["blockers"] == []
        assert report["checks"]["p4.4_5_b.metric_layer_decision_present"]["status"] == "PASS"
        assert report["checks"]["market_events_v2.p4_market_identity_present"]["status"] == "PASS"
        assert report["checks"]["p4.4_8.k2_daily_obs_ok"]["status"] == "PASS"
        assert report["checks"]["p4.4_8.k2_forecasts_daily_row_count_verified"]["status"] == "PASS"

    def test_p4_readiness_rejects_placeholder_operator_artifacts(self, tmp_path):
        db_path = tmp_path / "p4-placeholder-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=False)
        conn.commit()
        conn.close()
        state_dir = _ready_p4_state_dir(tmp_path)
        market_rule_file = tmp_path / "market_rule.json"
        market_rule_file.write_text("{}", encoding="utf-8")
        mx_dir = tmp_path / "tigge_mx"
        mn_dir = tmp_path / "tigge_mn"
        mx_dir.mkdir()
        mn_dir.mkdir()
        (mx_dir / "sample.json").write_text("{}", encoding="utf-8")
        (mn_dir / "sample.json").write_text("{}", encoding="utf-8")

        report = build_p4_readiness_report(
            db_path,
            state_dir=state_dir,
            env={"WU_API_KEY": "present"},
            market_rule_paths=(market_rule_file,),
            tigge_track_paths={
                "mx2t6_high": (mx_dir,),
                "mn2t6_low": (mn_dir,),
            },
            metric_layer_decision="daily-aggregate",
        )

        blockers = _blocker_codes(report)
        assert report["ready"] is False
        assert "p4_market_rule_acceptance_contract_missing" in blockers
        assert "p4_tigge_manifest_missing" in blockers

    def test_p4_readiness_blocks_unverified_runtime_evidence(self, tmp_path):
        db_path = tmp_path / "p4-runtime-world.db"
        conn = sqlite3.connect(db_path)
        _seed_minimal_ready_training_tables(conn, seed_observations=False)
        conn.commit()
        conn.close()
        state_dir = _ready_p4_state_dir(tmp_path)
        (state_dir / "status_summary.json").write_text(
            '{"risk":{"infrastructure_level":"YELLOW"}}',
            encoding="utf-8",
        )
        (state_dir / "k2_forecasts_daily_row_count.json").unlink()
        market_rule_file = tmp_path / "market_rule.json"
        _write_accepted_market_rule(market_rule_file)
        mx_dir = tmp_path / "tigge_mx"
        mn_dir = tmp_path / "tigge_mn"
        mx_dir.mkdir()
        mn_dir.mkdir()
        _write_accepted_tigge_manifest(mx_dir / "manifest.json", track="mx2t6_high")
        _write_accepted_tigge_manifest(mn_dir / "manifest.json", track="mn2t6_low")

        report = build_p4_readiness_report(
            db_path,
            state_dir=state_dir,
            env={"WU_API_KEY": "present"},
            market_rule_paths=(market_rule_file,),
            tigge_track_paths={
                "mx2t6_high": (mx_dir,),
                "mn2t6_low": (mn_dir,),
            },
            metric_layer_decision="daily-aggregate",
        )

        blockers = _blocker_codes(report)
        assert report["ready"] is False
        assert "p4_forecast_row_count_evidence_missing" in blockers
        assert "p4_status_summary_infrastructure_not_green" in blockers


class TestPortfolioTruthSource:
    """AB-003: canonical truth path must never silently degrade to fallback."""

    @pytest.mark.skip(reason="P9/Phase2: legacy position_events_legacy or backfill eliminated")
    def test_portfolio_truth_source_is_canonical(self):
        """Portfolio loader must return status 'ok' or 'partial_stale'.

        If this fails, position_current projections are missing or broken.
        'partial_stale' is acceptable when some legacy positions have newer
        events — those are excluded per-position while the rest are served.
        """
        conn = get_connection()
        result = query_portfolio_loader_view(conn)
        status = result.get("status", "unknown")
        assert status in ("ok", "partial_stale"), (
            f"portfolio_truth_source is '{status}', not 'ok'/'partial_stale'. "
            f"Stale trade IDs: {result.get('stale_trade_ids', [])}. "
            f"This means position_current is behind position_events_legacy."
        )

    def test_portfolio_loader_has_positions(self):
        """Portfolio loader must return at least one position when ok."""
        conn = get_connection()
        result = query_portfolio_loader_view(conn)
        if result.get("status") in ("ok", "partial_stale"):
            positions = result.get("positions", [])
            assert len(positions) > 0, "Status is ok but zero positions returned"


class TestGhostPositions:
    """Entered trade_decisions with expired target_dates are ghost positions."""

    def test_no_ghost_positions(self):
        """No trade_decisions with status=entered should have target_date in the past.

        Ghost positions indicate the decision-to-position materialization gap:
        a decision was entered but never reached durable position truth or was
        never voided/settled after expiry.
        """
        conn = get_trade_connection()
        today = date.today()
        rows = conn.execute(
            "SELECT trade_id, bin_label FROM trade_decisions WHERE status='entered'"
        ).fetchall()

        ghosts = []
        for row in rows:
            trade_id = row["trade_id"]
            bin_label = row["bin_label"] or ""
            date_m = re.search(r"on (\w+ \d+)\?", bin_label)
            if date_m:
                try:
                    target = datetime.strptime(
                        date_m.group(1) + ", 2026", "%B %d, %Y"
                    ).date()
                    if target < today:
                        ghosts.append(trade_id)
                except ValueError:
                    pass

        assert len(ghosts) == 0, (
            f"{len(ghosts)} ghost positions found (entered decisions with expired target_date): "
            f"{ghosts[:10]}{'...' if len(ghosts) > 10 else ''}"
        )


class TestSettlementFreshness:
    """Settlement lifecycle must keep pace with the trading cycle."""

    @pytest.mark.skip(reason="live DB health check — requires running Zeus instance with settlement data")
    def test_settlement_freshness(self):
        """Latest settlement activity must be within 48h.

        Checks decision_log settlement artifacts and calibration_pairs_v2,
        not the deprecated legacy settlements table.
        """
        conn = get_connection()
        max_settled = conn.execute(
            "SELECT MAX(timestamp) FROM decision_log WHERE mode = 'settlement'"
        ).fetchone()[0]

        calibration_table = "calibration_pairs_v2"
        max_cal_target = conn.execute(
            f"SELECT MAX(target_date) FROM {calibration_table}"
        ).fetchone()[0]

        assert max_settled is not None or max_cal_target is not None, (
            "No settlement activity found in decision_log or calibration_pairs_v2"
        )

        if max_settled:
            dt = _parse_iso(str(max_settled))
            if dt:
                age_hours = (_now_utc() - dt).total_seconds() / 3600
                assert age_hours <= 48, (
                    f"Settlement is {age_hours:.1f}h stale (threshold: 48h). "
                    f"Latest: {max_settled}"
                )


@pytest.mark.skip(reason="P9/Phase2: legacy position_events_legacy or backfill eliminated")
def test_portfolio_loader_ignores_same_phase_legacy_entry_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_MODE", "paper")
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at
        ) VALUES (
            'trade-1', 'active', 'trade-1', 'm1', 'NYC', 'NYC', '2099-04-01', '39-40°F',
            'buy_yes', 'F', 5.0, 14.29, 5.0, 0.35, 0.6,
            'snap-1', 'ens_member_counting', 'center_buy', 'center_buy', 'opening_hunt',
            'unknown', '', 'filled', '2099-04-01T11:45:45.242001+00:00'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events_legacy (
            event_type, runtime_trade_id, position_state, order_id, decision_snapshot_id,
            city, target_date, market_id, bin_label, direction, strategy, edge_source,
            source, details_json, timestamp, env
        ) VALUES (
            'ORDER_FILLED', 'trade-1', 'entered', '', 'snap-1',
            'NYC', '2099-04-01', 'm1', '39-40°F', 'buy_yes', 'center_buy', 'center_buy',
            'test', '{}', '2099-04-01T11:45:45.242861+00:00', 'paper'
        )
        """
    )
    conn.commit()

    result = query_portfolio_loader_view(conn)
    conn.close()

    assert result["status"] == "ok"
    assert [row["trade_id"] for row in result["positions"]] == ["trade-1"]


@pytest.mark.skip(reason="P9/Phase2: legacy position_events_legacy or backfill eliminated")
def test_portfolio_loader_marks_semantic_exit_shadow_as_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_MODE", "paper")
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at
        ) VALUES (
            'shadow-trade', 'day0_window', 'shadow-trade', 'm1', 'Dallas', 'Dallas', '2099-04-07', '76-77°F',
            'buy_no', 'F', 1.18, 1.28, 1.18, 0.92, 0.55,
            'snap-1', 'ens_member_counting', 'opening_inertia', 'opening_inertia', 'opening_hunt',
            'unknown', '', 'filled', '2099-04-07T10:58:44.847407+00:00'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events_legacy (
            event_type, runtime_trade_id, position_state, order_id, decision_snapshot_id,
            city, target_date, market_id, bin_label, direction, strategy, edge_source,
            source, details_json, timestamp, env
        ) VALUES (
            'POSITION_EXIT_RECORDED', 'shadow-trade', 'economically_closed', '', 'snap-1',
            'Dallas', '2099-04-07', 'm1', '76-77°F', 'buy_no', 'opening_inertia', 'opening_inertia',
            'test', '{\"pnl\":0.05}', '2099-04-07T11:14:30.687958+00:00', 'paper'
        )
        """
    )
    conn.commit()

    result = query_portfolio_loader_view(conn)
    conn.close()

    assert result["status"] == "stale_legacy_fallback"
    assert result["stale_trade_ids"] == ["shadow-trade"]


@pytest.mark.skip(reason="P9/Phase2: legacy position_events_legacy or backfill eliminated")
def test_portfolio_loader_keeps_older_semantic_advance_stale_even_if_newer_shadow_event_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_MODE", "paper")
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at
        ) VALUES (
            'pending-trade', 'pending_entry', 'pending-trade', 'm1', 'NYC', 'NYC', '2099-04-01', '39-40°F',
            'buy_yes', 'F', 5.0, 14.29, 5.0, 0.35, 0.6,
            'snap-1', 'ens_member_counting', 'center_buy', 'center_buy', 'opening_hunt',
            'unknown', '', 'filled', '2099-04-01T11:45:45.242001+00:00'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events_legacy (
            event_type, runtime_trade_id, position_state, order_id, decision_snapshot_id,
            city, target_date, market_id, bin_label, direction, strategy, edge_source,
            source, details_json, timestamp, env
        ) VALUES (
            'POSITION_ENTRY_RECORDED', 'pending-trade', 'entered', '', 'snap-1',
            'NYC', '2099-04-01', 'm1', '39-40°F', 'buy_yes', 'center_buy', 'center_buy',
            'test', '{}', '2099-04-01T11:45:46.000000+00:00', 'paper'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events_legacy (
            event_type, runtime_trade_id, position_state, order_id, decision_snapshot_id,
            city, target_date, market_id, bin_label, direction, strategy, edge_source,
            source, details_json, timestamp, env
        ) VALUES (
            'ORDER_FILLED', 'pending-trade', 'entered', '', 'snap-1',
            'NYC', '2099-04-01', 'm1', '39-40°F', 'buy_yes', 'center_buy', 'center_buy',
            'test', '{}', '2099-04-01T11:45:47.000000+00:00', 'paper'
        )
        """
    )
    conn.commit()

    result = query_portfolio_loader_view(conn)
    conn.close()

    assert result["status"] == "stale_legacy_fallback"
    assert result["stale_trade_ids"] == ["pending-trade"]
