"""Truth-surface health tests — antibodies for Venus sensing audit findings.

These tests verify cross-surface invariants that Venus detected as broken.
They run against the live Zeus database, not test fixtures, because the
invariants they check are about production state integrity.
"""
# Lifecycle: created=2026-04-07; last_reviewed=2026-04-24; last_reused=2026-04-24
# Purpose: Protect canonical truth surfaces and P0 training-readiness fail-closed checks.
# Reuse: Inspect high-sensitivity skip metadata and live-DB assumptions before treating full-file results as closeout evidence.

import re
import sqlite3
from datetime import date, datetime, timezone

import pytest

from src.state.db import get_connection, init_schema, query_portfolio_loader_view
from src.state.schema.v2_schema import apply_v2_schema
from scripts import verify_truth_surfaces as truth_surfaces
from scripts.verify_truth_surfaces import build_training_readiness_report


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


def _seed_minimal_ready_training_tables(conn, *, seed_observations=True):
    for table in [
        "forecasts",
        "calibration_pairs_v2",
        "platt_models_v2",
    ]:
        conn.execute(f"CREATE TABLE {table} (id INTEGER)")
        conn.execute(f"INSERT INTO {table} (id) VALUES (1)")
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
        "CREATE TABLE observation_instants_v2 (training_allowed INTEGER, source_role TEXT)"
    )
    conn.execute(
        "INSERT INTO observation_instants_v2 (training_allowed, source_role) VALUES (1, 'historical_hourly')"
    )
    if seed_observations:
        conn.execute("CREATE TABLE observations (authority TEXT, provenance_metadata TEXT)")
        conn.execute(
            "INSERT INTO observations (authority, provenance_metadata) VALUES ('VERIFIED', '{}')"
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
            "INSERT INTO observation_instants_v2 (training_allowed, source_role) VALUES (1, 'banana')"
        )
        conn.commit()
        conn.close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        assert "empty_training_eligible_observations" in _blocker_codes(report)
        assert "fallback_source_role" in _blocker_codes(report)
        assert report["checks"]["observation_instants_v2.source_role_canonical"]["count"] == 1

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
        assert "empty_observation_provenance" in _blocker_codes(report)
        check = report["checks"]["observations.provenance_present"]
        assert check["status"] == "FAIL"
        assert check["count"] == 1

    def test_training_readiness_fails_when_required_eligibility_tables_are_missing(self, tmp_path):
        db_path = tmp_path / "raw-world.db"
        sqlite3.connect(db_path).close()

        report = build_training_readiness_report(db_path)

        assert report["ready"] is False
        checks = report["checks"]
        for check_id in [
            "observation_instants_v2.source_role_canonical",
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

        assert "fallback_source_role" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.source_role_canonical"]
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

        assert "fallback_source_role" in _blocker_codes(report)
        check = report["checks"]["observation_instants_v2.source_role_canonical"]
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
        check = report["checks"]["observation_instants_v2.source_role_canonical"]
        assert check["status"] == "FAIL"

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
        conn = get_connection()
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

        Checks decision_log settlement artifacts and calibration_pairs,
        not the deprecated legacy settlements table.
        """
        conn = get_connection()
        max_settled = conn.execute(
            "SELECT MAX(timestamp) FROM decision_log WHERE mode = 'settlement'"
        ).fetchone()[0]

        max_cal_target = conn.execute(
            "SELECT MAX(target_date) FROM calibration_pairs"
        ).fetchone()[0]

        assert max_settled is not None or max_cal_target is not None, (
            "No settlement activity found in decision_log or calibration_pairs"
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
            'trade-1', 'active', 'trade-1', 'm1', 'NYC', 'US-Northeast', '2099-04-01', '39-40°F',
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
            'shadow-trade', 'day0_window', 'shadow-trade', 'm1', 'Dallas', 'US-South', '2099-04-07', '76-77°F',
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
            'pending-trade', 'pending_entry', 'pending-trade', 'm1', 'NYC', 'US-Northeast', '2099-04-01', '39-40°F',
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
