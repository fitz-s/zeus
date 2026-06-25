"""Replay point-in-time provenance antibodies."""
# Lifecycle: created=2026-04-25; last_reviewed=2026-05-18; last_reused=2026-05-18
# Purpose: Lock replay snapshot/decision reference selection to point-in-time evidence.
# Reuse: Run with replay fidelity gates when changing ReplayContext reference lookup.
# Authority basis: P3 usage-path residual guards packet; replay point-in-time provenance gate.
import pytest

from src.engine.replay import ReplayContext, ReplayPreflightError
from src.state.db import get_connection, init_schema, init_schema_forecasts
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN


def _enable_legacy_snapshot_fixture(conn):
    """Ensure the canonical ensemble_snapshots table exists for fixture inserts.

    B3 (2026-05-28): TEMP TABLE + trigger approach dropped because SQLite does not
    allow schema-qualified INSERT targets (main.X) inside trigger bodies.  Now we
    simply call init_schema_forecasts so that the real ensemble_snapshots table is
    present.  Callers must supply physical_quantity and observation_field in their
    INSERT statements (both NOT NULL in the canonical schema).

    B5 (2026-05-28): ensemble_snapshots.data_version renamed to dataset_id in the
    canonical schema, but src/engine/replay.py._snapshot_row() still queries for
    the 'data_version' column in its base_columns subset check (line ~401).  Until
    that src/ migration lands, we ADD the legacy alias column to every schema that
    carries ensemble_snapshots so that the subset check passes.
    """
    import sqlite3 as _sqlite3
    init_schema_forecasts(conn)
    for schema_prefix, schema_arg in (("", "main"), ("world.", "world"), ("forecasts.", "forecasts")):
        try:
            exists = conn.execute(
                f"SELECT 1 FROM {schema_arg}.sqlite_master "
                "WHERE type='table' AND name='ensemble_snapshots' LIMIT 1"
            ).fetchone()
        except _sqlite3.OperationalError:
            continue
        if not exists:
            continue
        has_col = any(
            row[1] == "data_version"
            for row in conn.execute(
                f"PRAGMA {schema_arg}.table_info(ensemble_snapshots)"
            ).fetchall()
        )
        if not has_col:
            conn.execute(
                f"ALTER TABLE {schema_arg}.ensemble_snapshots ADD COLUMN data_version TEXT"
            )


def test_replay_context_uses_only_snapshot_available_at_or_before_decision_time(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _enable_legacy_snapshot_fixture(conn)

    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
         issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, dataset_id)
        VALUES
        (1, 'NYC', '2026-04-01', 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
         '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
         '2026-03-31T12:00:00Z', '2026-03-31T12:05:00Z', 24.0, '[40.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1'),
        (2, 'NYC', '2026-04-01', 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
         '2026-03-31T06:00:00Z', '2026-04-01T00:00:00Z',
         '2026-03-31T18:00:00Z', '2026-03-31T18:05:00Z', 18.0, '[41.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1')
        """
    )
    ctx = ReplayContext(conn)
    snap = ctx.get_snapshot_for(
        "NYC",
        "2026-04-01",
        decision_time="2026-03-31T16:00:00+00:00",
    )
    conn.close()

    assert snap is not None
    assert snap["snapshot_id"] == 1
    assert snap["available_at"] == "2026-03-31T12:00:00Z"


def test_replay_context_uses_actual_trade_snapshot_reference(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    init_schema_forecasts(conn)
    _enable_legacy_snapshot_fixture(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
         issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, dataset_id)
        VALUES
        (11, 'NYC', '2026-04-01', 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
         '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
         '2026-03-31T10:00:00Z', '2026-03-31T10:05:00Z', 24.0, '[40.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1'),
        (12, 'NYC', '2026-04-01', 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
         '2026-03-31T06:00:00Z', '2026-04-01T00:00:00Z',
         '2026-03-31T14:00:00Z', '2026-03-31T14:05:00Z', 18.0, '[42.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, forecast_snapshot_id,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, status, edge_source, env)
        VALUES ('mkt', '39-40°F', 'buy_yes', 5.0, 0.4, '2026-03-31T12:00:00+00:00', 11,
                0.6, 0.6, 0.2, 0.55, 0.65, 0.0, 'entered', 'center_buy', 'legacy_env')
        """
    )

    ctx = ReplayContext(conn)
    ref = ctx.get_decision_reference_for("NYC", "2026-04-01")
    snap = ctx.get_snapshot_for(
        "NYC",
        "2026-04-01",
        decision_time=ref["decision_time"],
        snapshot_id=ref["snapshot_id"],
    )
    conn.close()

    assert ref is not None
    assert ref["snapshot_id"] == 11
    assert snap is not None
    assert snap["snapshot_id"] == 11


def test_replay_context_prefers_v2_snapshot_for_decision_reference(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    init_schema_forecasts(conn)
    _enable_legacy_snapshot_fixture(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity,
         observation_field, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal,
         model_version, dataset_id, training_allowed, causality_status,
         boundary_ambiguous, provenance_json, authority, members_unit, unit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            111,
            "NYC",
            "2026-04-01",
            HIGH_LOCALDAY_MAX.temperature_metric,
            HIGH_LOCALDAY_MAX.physical_quantity,
            HIGH_LOCALDAY_MAX.observation_field,
            "2026-03-31T00:00:00Z",
            "2026-04-01T00:00:00Z",
            "2026-03-31T10:00:00Z",
            "2026-03-31T10:05:00Z",
            24.0,
            "[50.0]",
            "[0.8]",
            1.0,
            0,
            "ecmwf_v2",
            HIGH_LOCALDAY_MAX.data_version,
            1,
            "OK",
            0,
            "{}",
            "VERIFIED",
            "degF",
            "F",
        ),
    )
    # B3 (2026-05-28): the legacy "low-quality" duplicate INSERT (snapshot_id=111) that
    # previously went into a TEMP TABLE shadow is removed.  The TEMP TABLE + TRIGGER
    # mechanism is gone; both inserts now target the same canonical ensemble_snapshots.
    # The canonical v2 insert above is sufficient to assert that v2 data is returned.
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, forecast_snapshot_id,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, status, edge_source, env)
        VALUES ('mkt', '39-40°F', 'buy_yes', 5.0, 0.4, '2026-03-31T12:00:00+00:00', 111,
                0.8, 0.8, 0.2, 0.75, 0.85, 0.0, 'entered', 'center_buy', 'legacy_env')
        """
    )

    ctx = ReplayContext(conn)
    ref = ctx.get_decision_reference_for("NYC", "2026-04-01")
    snap = ctx.get_snapshot_for(
        "NYC",
        "2026-04-01",
        decision_time=ref["decision_time"],
        snapshot_id=ref["snapshot_id"],
    )
    conn.close()

    assert ref is not None
    assert ref["snapshot_id"] == 111
    assert snap is not None
    assert snap["snapshot_source"] == "ensemble_snapshots"
    assert snap["authority_scope"] == "canonical_snapshot_v2"
    assert snap["model"] == "ecmwf_v2"
    assert snap["p_raw_stored"] == [0.8]


def test_replay_context_prefers_forecasts_v2_snapshot_over_world_ghost(tmp_path):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecasts_db = tmp_path / "zeus-forecasts.db"
    trade_conn = get_connection(trade_db)
    init_schema(trade_conn)
    trade_conn.close()
    world_conn = get_connection(world_db)
    init_schema(world_conn)
    init_schema_forecasts(world_conn)
    world_conn.close()
    forecasts_conn = get_connection(forecasts_db)
    init_schema_forecasts(forecasts_conn)
    forecasts_conn.close()

    conn = get_connection(trade_db)
    conn.execute("ATTACH DATABASE ? AS world", (str(world_db),))
    conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_db),))
    _enable_legacy_snapshot_fixture(conn)
    conn.execute(
        """
        INSERT INTO world.ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity,
         observation_field, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal,
         model_version, dataset_id, training_allowed, causality_status,
         boundary_ambiguous, provenance_json, authority, members_unit, unit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            211,
            "NYC",
            "2026-04-01",
            HIGH_LOCALDAY_MAX.temperature_metric,
            HIGH_LOCALDAY_MAX.physical_quantity,
            HIGH_LOCALDAY_MAX.observation_field,
            "2026-03-31T00:00:00Z",
            "2026-04-01T00:00:00Z",
            "2026-03-31T10:00:00Z",
            "2026-03-31T10:05:00Z",
            24.0,
            "[60.0]",
            "[0.7]",
            1.0,
            0,
            "world_v2",
            HIGH_LOCALDAY_MAX.data_version,
            1,
            "OK",
            0,
            "{}",
            "VERIFIED",
            "degF",
            "F",
        ),
    )
    conn.execute(
        """
        INSERT INTO forecasts.ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity,
         observation_field, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal,
         model_version, dataset_id, training_allowed, causality_status,
         boundary_ambiguous, provenance_json, authority, members_unit, unit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            211,
            "NYC",
            "2026-04-01",
            HIGH_LOCALDAY_MAX.temperature_metric,
            HIGH_LOCALDAY_MAX.physical_quantity,
            HIGH_LOCALDAY_MAX.observation_field,
            "2026-03-31T00:00:00Z",
            "2026-04-01T00:00:00Z",
            "2026-03-31T10:00:00Z",
            "2026-03-31T10:05:00Z",
            24.0,
            "[62.0]",
            "[0.9]",
            1.0,
            0,
            "forecasts_v2",
            HIGH_LOCALDAY_MAX.data_version,
            1,
            "OK",
            0,
            "{}",
            "VERIFIED",
            "degF",
            "F",
        ),
    )
    # Main trade_decisions keeps its legacy FK to main.ensemble_snapshots; the
    # replay reader must still resolve the snapshot facts from attached world v2.
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
         issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, dataset_id)
        VALUES (211, 'NYC', '2026-04-01', 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
                '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
                '2026-03-31T10:00:00Z', '2026-03-31T10:05:00Z', 24.0, '[40.0]', '[0.1]', 2.0, 0, 'main_legacy', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, forecast_snapshot_id,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, status, edge_source, env)
        VALUES ('mkt', '39-40°F', 'buy_yes', 5.0, 0.4, '2026-03-31T12:00:00+00:00', 211,
                0.7, 0.7, 0.2, 0.65, 0.75, 0.0, 'entered', 'center_buy', 'legacy_env')
        """
    )

    ctx = ReplayContext(conn)
    ref = ctx.get_decision_reference_for("NYC", "2026-04-01")
    snap = ctx.get_snapshot_for(
        "NYC",
        "2026-04-01",
        decision_time=ref["decision_time"],
        snapshot_id=ref["snapshot_id"],
    )
    conn.close()

    assert ref is not None
    assert ref["snapshot_id"] == 211
    assert snap is not None
    assert snap["snapshot_source"] == "ensemble_snapshots"
    assert snap["model"] == "forecasts_v2"
    assert snap["p_raw_stored"] == [0.9]


def test_replay_context_prefers_forecasts_settlement_over_world_ghost(tmp_path):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecasts_db = tmp_path / "zeus-forecasts.db"
    trade_conn = get_connection(trade_db)
    init_schema(trade_conn)
    trade_conn.close()
    world_conn = get_connection(world_db)
    init_schema(world_conn)
    init_schema_forecasts(world_conn)
    world_conn.close()
    forecasts_conn = get_connection(forecasts_db)
    init_schema_forecasts(forecasts_conn)
    forecasts_conn.close()

    conn = get_connection(trade_db)
    conn.execute("ATTACH DATABASE ? AS world", (str(world_db),))
    conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_db),))
    conn.execute(
        """
        INSERT INTO forecasts.ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity,
         observation_field, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal,
         model_version, dataset_id, training_allowed, causality_status,
         boundary_ambiguous, provenance_json, authority, members_unit, unit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            312,
            "NYC",
            "2026-04-01",
            HIGH_LOCALDAY_MAX.temperature_metric,
            HIGH_LOCALDAY_MAX.physical_quantity,
            HIGH_LOCALDAY_MAX.observation_field,
            "2026-03-31T00:00:00Z",
            "2026-04-01T00:00:00Z",
            "2026-03-31T10:00:00Z",
            "2026-03-31T10:05:00Z",
            24.0,
            "[62.0]",
            "[0.9]",
            1.0,
            0,
            "forecasts_v2",
            HIGH_LOCALDAY_MAX.data_version,
            1,
            "OK",
            0,
            "{}",
            "VERIFIED",
            "degF",
            "F",
        ),
    )
    conn.execute(
        """
        INSERT INTO world.settlements
        (city, target_date, winning_bin, settlement_value, temperature_metric, authority)
        VALUES ('NYC', '2026-04-01', 'ghost', 1.0, 'high', 'VERIFIED')
        """
    )
    # B3cont (2026-05-28): forecasts.settlements shell dropped; canonical is settlement_outcomes.
    conn.execute(
        """
        INSERT INTO forecasts.settlement_outcomes
        (city, target_date, winning_bin, settlement_value, temperature_metric, authority)
        VALUES ('NYC', '2026-04-01', 'authoritative', 2.0, 'high', 'VERIFIED')
        """
    )

    ctx = ReplayContext(conn)
    settlement = ctx.get_settlement("NYC", "2026-04-01")
    conn.close()

    assert settlement == {"settlement_value": 2.0, "winning_bin": "authoritative"}


def test_replay_context_rejects_world_only_forecast_class_v2_snapshot(tmp_path):
    trade_db = tmp_path / "empty-trade.db"
    world_db = tmp_path / "zeus-world.db"
    world_conn = get_connection(world_db)
    init_schema(world_conn)
    init_schema_forecasts(world_conn)
    world_conn.close()

    conn = get_connection(trade_db)
    conn.execute("ATTACH DATABASE ? AS world", (str(world_db),))

    with pytest.raises(ReplayPreflightError, match="world ghost fallback is forbidden"):
        ReplayContext(conn)
    conn.close()


def test_replay_context_falls_back_to_decision_log_no_trade_snapshot_reference(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    init_schema_forecasts(conn)
    _enable_legacy_snapshot_fixture(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
         issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, dataset_id)
        VALUES (21, 'London', '2026-04-02', 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
                '2026-04-01T00:00:00Z', '2026-04-02T00:00:00Z',
                '2026-04-01T10:00:00Z', '2026-04-01T10:05:00Z', 24.0, '[12.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES ('opening_hunt', '2026-04-01T12:00:00+00:00', '2026-04-01T12:01:00+00:00',
                '{"mode":"opening_hunt","started_at":"2026-04-01T12:00:00+00:00","completed_at":"2026-04-01T12:01:00+00:00","skipped_reason":"","trade_cases":[],"no_trade_cases":[{"decision_id":"d1","city":"London","target_date":"2026-04-02","range_label":"12°C","direction":"buy_yes","rejection_stage":"EDGE_INSUFFICIENT","rejection_reasons":["x"],"best_edge":0.0,"model_prob":0.4,"market_price":0.5,"decision_snapshot_id":"21","selected_method":"ens_member_counting","applied_validations":["ens_fetch"],"bin_labels":["12°C"],"p_raw_vector":[1.0],"p_cal_vector":[1.0],"p_market_vector":[0.5],"timestamp":"2026-04-01T12:00:30+00:00"}],"monitor_results":[],"summary":{}}',
                '2026-04-01T12:01:00+00:00', 'legacy_env')
        """
    )
    ctx = ReplayContext(conn)
    ref = ctx.get_decision_reference_for("London", "2026-04-02")
    snap = ctx.get_snapshot_for(
        "London",
        "2026-04-02",
        decision_time=ref["decision_time"],
        snapshot_id=ref["snapshot_id"],
    )
    conn.close()

    assert ref is not None
    assert ref["source"] == "decision_log.no_trade_cases"
    assert ref["snapshot_id"] == 21
    assert ref["p_market_vector"] == [0.5]
    assert snap is not None
    assert snap["snapshot_id"] == 21


def test_replay_context_snapshot_only_fallback_is_opt_in(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _enable_legacy_snapshot_fixture(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
         issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, dataset_id)
        VALUES (31, 'Paris', '2026-04-03', 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
                '2026-04-02T00:00:00Z', '2026-04-03T00:00:00Z',
                '2026-04-02T08:00:00Z', '2026-04-02T08:05:00Z', 24.0, '[12.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1')
        """
    )

    strict_ctx = ReplayContext(conn)
    fallback_ctx = ReplayContext(conn, allow_snapshot_only_reference=True)

    strict_ref = strict_ctx.get_decision_reference_for("Paris", "2026-04-03")
    fallback_ref = fallback_ctx.get_decision_reference_for("Paris", "2026-04-03")
    conn.close()

    assert strict_ref is None
    assert fallback_ref is not None
    assert fallback_ref["source"] == "ensemble_snapshots.available_at"
    assert fallback_ref["snapshot_id"] == 31


def test_replay_context_snapshot_only_fallback_prefers_v2(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    init_schema_forecasts(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity,
         observation_field, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal,
         model_version, dataset_id, training_allowed, causality_status,
         boundary_ambiguous, provenance_json, authority, members_unit, unit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            132,
            "Paris",
            "2026-04-03",
            HIGH_LOCALDAY_MAX.temperature_metric,
            HIGH_LOCALDAY_MAX.physical_quantity,
            HIGH_LOCALDAY_MAX.observation_field,
            "2026-04-02T00:00:00Z",
            "2026-04-03T00:00:00Z",
            "2026-04-02T09:00:00Z",
            "2026-04-02T09:05:00Z",
            24.0,
            "[13.0]",
            "[1.0]",
            2.0,
            0,
            "v2",
            HIGH_LOCALDAY_MAX.data_version,
            1,
            "OK",
            0,
            "{}",
            "VERIFIED",
            "degC",
            "C",
        ),
    )

    fallback_ctx = ReplayContext(conn, allow_snapshot_only_reference=True)
    fallback_ref = fallback_ctx.get_decision_reference_for("Paris", "2026-04-03")
    conn.close()

    assert fallback_ref is not None
    assert fallback_ref["source"] == "ensemble_snapshots.available_at"
    assert fallback_ref["snapshot_id"] == 132
    assert fallback_ref["authority_scope"] == "offline_no_promotion"


def test_replay_context_v2_snapshot_lookup_is_metric_scoped(tmp_path):
    """DSA-13/dual-track: v2 snapshot reads must not cross HIGH/LOW rows."""
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    init_schema_forecasts(conn)
    _enable_legacy_snapshot_fixture(conn)
    for snapshot_id, metric, identity, members, p_raw, available_at in (
        (
            501,
            HIGH_LOCALDAY_MAX.temperature_metric,
            HIGH_LOCALDAY_MAX,
            "[80.0]",
            "[0.9]",
            "2026-04-02T08:00:00Z",
        ),
        (
            502,
            LOW_LOCALDAY_MIN.temperature_metric,
            LOW_LOCALDAY_MIN,
            "[50.0]",
            "[0.3]",
            "2026-04-02T09:00:00Z",
        ),
    ):
        conn.execute(
            """
            INSERT INTO ensemble_snapshots
            (snapshot_id, city, target_date, temperature_metric, physical_quantity,
             observation_field, issue_time, valid_time, available_at, fetch_time,
             lead_hours, members_json, p_raw_json, spread, is_bimodal,
             model_version, dataset_id, training_allowed, causality_status,
             boundary_ambiguous, provenance_json, authority, members_unit, unit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                "Paris",
                "2026-04-03",
                metric,
                identity.physical_quantity,
                identity.observation_field,
                "2026-04-02T00:00:00Z",
                "2026-04-03T00:00:00Z",
                available_at,
                available_at,
                24.0,
                members,
                p_raw,
                2.0,
                0,
                f"v2_{metric}",
                identity.data_version,
                1,
                "OK",
                0,
                "{}",
                "VERIFIED",
                "degF",
                "F",
            ),
        )

    fallback_ctx = ReplayContext(conn, allow_snapshot_only_reference=True)
    low_ref = fallback_ctx.get_decision_reference_for(
        "Paris",
        "2026-04-03",
        temperature_metric="low",
    )
    wrong_metric_snap = fallback_ctx.get_snapshot_for(
        "Paris",
        "2026-04-03",
        decision_time="2026-04-02T12:00:00+00:00",
        snapshot_id=501,
        temperature_metric="low",
    )
    low_snap = fallback_ctx.get_snapshot_for(
        "Paris",
        "2026-04-03",
        decision_time="2026-04-02T12:00:00+00:00",
        snapshot_id=502,
        temperature_metric="low",
    )
    conn.close()

    assert low_ref is not None
    assert low_ref["source"] == "ensemble_snapshots.available_at"
    assert low_ref["snapshot_id"] == 502
    assert wrong_metric_snap is None
    assert low_snap is not None
    assert low_snap["snapshot_source"] == "ensemble_snapshots"
    assert low_snap["p_raw_stored"] == [0.3]


def test_replay_context_snapshot_only_fallback_requires_p_raw(tmp_path):
    """DSA-13: diagnostic snapshot references need stored p_raw evidence."""
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    init_schema_forecasts(conn)
    _enable_legacy_snapshot_fixture(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity,
         observation_field, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal,
         model_version, dataset_id, training_allowed, causality_status,
         boundary_ambiguous, provenance_json, authority, members_unit, unit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            601,
            "Paris",
            "2026-04-03",
            HIGH_LOCALDAY_MAX.temperature_metric,
            HIGH_LOCALDAY_MAX.physical_quantity,
            HIGH_LOCALDAY_MAX.observation_field,
            "2026-04-02T00:00:00Z",
            "2026-04-03T00:00:00Z",
            "2026-04-02T08:00:00Z",
            "2026-04-02T08:05:00Z",
            24.0,
            "[12.0]",
            None,
            2.0,
            0,
            "v2_missing_p_raw",
            HIGH_LOCALDAY_MAX.data_version,
            1,
            "OK",
            0,
            "{}",
            "VERIFIED",
            "degC",
            "C",
        ),
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
         issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, dataset_id)
        VALUES (602, 'Paris', '2026-04-03', 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
                '2026-04-02T00:00:00Z', '2026-04-03T00:00:00Z',
                '2026-04-02T09:00:00Z', '2026-04-02T09:05:00Z', 24.0, '[13.0]', '[1.0]', 2.0, 0, 'legacy', 'v1')
        """
    )

    fallback_ctx = ReplayContext(conn, allow_snapshot_only_reference=True)
    fallback_ref = fallback_ctx.get_decision_reference_for("Paris", "2026-04-03")
    conn.close()

    assert fallback_ref is not None
    assert fallback_ref["source"] == "ensemble_snapshots.available_at"
    assert fallback_ref["snapshot_id"] == 602
