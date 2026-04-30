"""Replay point-in-time provenance antibodies."""
# Lifecycle: created=2026-04-25; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: Lock replay snapshot/decision reference selection to point-in-time evidence.
# Reuse: Run with replay fidelity gates when changing ReplayContext reference lookup.
# Authority basis: P3 usage-path residual guards packet; replay point-in-time provenance gate.
from src.engine.replay import ReplayContext
from src.state.db import get_connection, init_schema


def test_replay_context_uses_only_snapshot_available_at_or_before_decision_time(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES
        (1, 'NYC', '2026-04-01', '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
         '2026-03-31T12:00:00Z', '2026-03-31T12:05:00Z', 24.0, '[40.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1'),
        (2, 'NYC', '2026-04-01', '2026-03-31T06:00:00Z', '2026-04-01T00:00:00Z',
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
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES
        (11, 'NYC', '2026-04-01', '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
         '2026-03-31T10:00:00Z', '2026-03-31T10:05:00Z', 24.0, '[40.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1'),
        (12, 'NYC', '2026-04-01', '2026-03-31T06:00:00Z', '2026-04-01T00:00:00Z',
         '2026-03-31T14:00:00Z', '2026-03-31T14:05:00Z', 18.0, '[42.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, forecast_snapshot_id,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, status, edge_source, env)
        VALUES ('mkt', '39-40°F', 'buy_yes', 5.0, 0.4, '2026-03-31T12:00:00+00:00', 11,
                0.6, 0.6, 0.2, 0.55, 0.65, 0.0, 'entered', 'center_buy', 'paper')
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


def test_replay_context_falls_back_to_decision_log_no_trade_snapshot_reference(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (21, 'London', '2026-04-02', '2026-04-01T00:00:00Z', '2026-04-02T00:00:00Z',
                '2026-04-01T10:00:00Z', '2026-04-01T10:05:00Z', 24.0, '[12.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES ('opening_hunt', '2026-04-01T12:00:00+00:00', '2026-04-01T12:01:00+00:00',
                '{"mode":"opening_hunt","started_at":"2026-04-01T12:00:00+00:00","completed_at":"2026-04-01T12:01:00+00:00","skipped_reason":"","trade_cases":[],"no_trade_cases":[{"decision_id":"d1","city":"London","target_date":"2026-04-02","range_label":"12°C","direction":"buy_yes","rejection_stage":"EDGE_INSUFFICIENT","rejection_reasons":["x"],"best_edge":0.0,"model_prob":0.4,"market_price":0.5,"decision_snapshot_id":"21","selected_method":"ens_member_counting","applied_validations":["ens_fetch"],"bin_labels":["12°C"],"p_raw_vector":[1.0],"p_cal_vector":[1.0],"p_market_vector":[0.5],"timestamp":"2026-04-01T12:00:30+00:00"}],"monitor_results":[],"summary":{}}',
                '2026-04-01T12:01:00+00:00', 'paper')
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
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (31, 'Paris', '2026-04-03', '2026-04-02T00:00:00Z', '2026-04-03T00:00:00Z',
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


def test_replay_context_can_fallback_to_shadow_signal_reference(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (41, 'Dallas', '2026-04-05', '2026-04-04T00:00:00Z', '2026-04-05T00:00:00Z',
                '2026-04-04T08:00:00Z', '2026-04-04T08:05:00Z', 24.0, '[12.0]', '[0.1,0.9]', 2.0, 0, 'ecmwf', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO shadow_signals
        (city, target_date, timestamp, decision_snapshot_id, p_raw_json, p_cal_json, edges_json, lead_hours)
        VALUES ('Dallas', '2026-04-05', '2026-04-04T10:00:00+00:00', '41',
                '[0.1, 0.9]', '[0.15, 0.85]',
                '[{\"bin_label\":\"39-40°F\"},{\"bin_label\":\"41-42°F\"}]',
                14.0)
        """
    )
    fallback_ctx = ReplayContext(conn, allow_snapshot_only_reference=True)
    ref = fallback_ctx.get_decision_reference_for("Dallas", "2026-04-05")
    conn.close()

    assert ref is not None
    assert ref["source"] == "legacy_shadow_signal_diagnostic"
    assert ref["storage_source"] == "shadow_signals"
    assert ref["authority_scope"] == "diagnostic_non_promotion"
    assert ref["snapshot_id"] == 41
    assert ref["bin_labels"] == ["39-40°F", "41-42°F"]
    assert ref["p_cal_vector"] == [0.15, 0.85]
