# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 no-submit redemption proof; reactor must not use venue or broad cycle runtime.
from __future__ import annotations

import ast
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from src.engine.event_reactor_adapter import (
    build_event_bound_no_submit_receipt,
    edli_source_truth_gate,
    edli_trade_score_gate,
    executable_snapshot_gate_from_trade_conn,
)
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.riskguard.risk_level import RiskLevel


def _forecast_event(completeness: str = "COMPLETE"):
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-25",
        metric="high",
        source_id="ecmwf_open_data",
        source_run_id="run-1",
        cycle="2026-05-24T00:00:00+00:00",
        track="operational",
        snapshot_id="1",
        snapshot_hash="hash-1",
        captured_at="2026-05-24T08:00:00+00:00",
        available_at="2026-05-24T08:10:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status=completeness,  # type: ignore[arg-type]
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-25|high|run-1",
        source="forecast_snapshot_ready_trigger",
        observed_at=payload.captured_at,
        available_at=payload.available_at,
        received_at="2026-05-24T08:11:00+00:00",
        causal_snapshot_id=payload.snapshot_id,
        payload=payload,
    )


def _bound_forecast_event(*, token_id: str = "yes-1", fdr_condition_count: int = 2):
    event = _forecast_event()
    payload = json.loads(event.payload_json)
    condition_id = "condition-2" if token_id.endswith("-2") else "condition-1"
    payload.update(
        {
            "condition_id": condition_id,
            "token_id": token_id,
            "unit": "F",
        }
    )
    return replace(event, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _trade_conn_with_snapshot(
    *,
    selected_ask: str = "0.40",
    no_selected_ask: str = "0.80",
    condition_count: int = 2,
    snapshot_condition_count: int | None = None,
):
    if snapshot_condition_count is None:
        snapshot_condition_count = condition_count
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            condition_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            selected_outcome_token_id TEXT,
            outcome_label TEXT,
            orderbook_top_ask TEXT NOT NULL,
            orderbook_top_bid TEXT NOT NULL DEFAULT '0.39',
            orderbook_depth_json TEXT NOT NULL DEFAULT '{}',
            min_tick_size TEXT NOT NULL DEFAULT '0.01',
            min_order_size TEXT NOT NULL DEFAULT '5',
            fee_details_json TEXT NOT NULL DEFAULT '{}',
            neg_risk INTEGER NOT NULL DEFAULT 0,
            freshness_deadline TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            active INTEGER NOT NULL,
            closed INTEGER NOT NULL,
            event_slug TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshots VALUES (
            'snapshot-exec-1', 'condition-1', 'yes-1', 'no-1', 'yes-1', 'YES',
            ?, '0.39', ?, '0.01', '5', '{"fee_rate_fraction":0.0}', 0, '2026-05-25T00:00:00+00:00',
            '2026-05-24T08:12:00+00:00', 1, 0, 'chicago-temperature-high'
        )
        """,
        (
            selected_ask,
            json.dumps(
                {
                    "YES": {"asks": [{"price": selected_ask, "size": "100"}], "bids": [{"price": "0.39", "size": "100"}]},
                    "NO": {"asks": [{"price": no_selected_ask, "size": "100"}], "bids": [{"price": "0.19", "size": "100"}]},
                },
                separators=(",", ":"),
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshots VALUES (
            'snapshot-exec-1-no', 'condition-1', 'yes-1', 'no-1', 'no-1', 'NO',
            ?, '0.19', ?, '0.01', '5', '{"fee_rate_fraction":0.0}', 0, '2026-05-25T00:00:00+00:00',
            '2026-05-24T08:12:00+00:00', 1, 0, 'chicago-temperature-high'
        )
        """,
        (
            no_selected_ask,
            json.dumps(
                {
                    "YES": {"asks": [{"price": selected_ask, "size": "100"}], "bids": [{"price": "0.39", "size": "100"}]},
                    "NO": {"asks": [{"price": no_selected_ask, "size": "100"}], "bids": [{"price": "0.19", "size": "100"}]},
                },
                separators=(",", ":"),
            ),
        ),
    )
    for index in range(2, snapshot_condition_count + 1):
        conn.execute(
            """
            INSERT INTO executable_market_snapshots VALUES (
                ?, ?, ?, ?, ?, 'YES',
                '0.48', '0.47', ?, '0.01', '5', '{"fee_rate_fraction":0.0}', 0, '2026-05-25T00:00:00+00:00',
                '2026-05-24T08:12:00+00:00', 1, 0, 'chicago-temperature-high'
            )
            """,
            (
                f"snapshot-exec-{index}",
                f"condition-{index}",
                f"yes-{index}",
                f"no-{index}",
                f"yes-{index}",
                json.dumps(
                    {
                        "YES": {"asks": [{"price": "0.48", "size": "100"}], "bids": [{"price": "0.47", "size": "100"}]},
                        "NO": {"asks": [{"price": "0.60", "size": "100"}], "bids": [{"price": "0.40", "size": "100"}]},
                    },
                    separators=(",", ":"),
                ),
            ),
        )
    conn.execute(
        """
        CREATE TABLE market_events_v2 (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            outcome TEXT,
            condition_id TEXT,
            token_id TEXT,
            market_slug TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL
        )
        """
    )
    rows = []
    for index in range(1, condition_count + 1):
        rows.append(
            (
                f"{70 + index - 1}-{71 + index - 1}°F",
                f"condition-{index}",
                f"yes-{index}",
                f"chicago-high-{index}",
                f"{70 + index - 1}-{71 + index - 1}°F",
                float(70 + index - 1),
                float(71 + index - 1),
            )
        )
    conn.executemany(
        """
        INSERT INTO market_events_v2 VALUES (
            'Chicago', '2026-05-25', 'high', ?, ?, ?, ?, ?, ?, ?
        )
        """,
        rows,
    )
    conn.execute(
        """
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id TEXT PRIMARY KEY,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            members_json TEXT NOT NULL,
            p_raw_json TEXT,
            p_cal_json TEXT,
            p_cal_model_key TEXT,
            p_cal_model_version TEXT,
            p_cal_authority TEXT,
            p_cal_available_at TEXT,
            p_cal_source_id TEXT,
            p_cal_source_run_id TEXT,
            members_unit TEXT,
            settlement_unit TEXT,
            source_id TEXT,
            source_transport TEXT,
            source_run_id TEXT,
            release_calendar_key TEXT,
            source_cycle_time TEXT,
            source_release_time TEXT,
            source_available_at TEXT,
            issue_time TEXT,
            valid_time TEXT,
            fetch_time TEXT,
            manifest_hash TEXT,
            lead_hours REAL,
            data_version TEXT,
            local_day_start_utc TEXT,
            step_horizon_hours REAL,
            first_member_observed_time TEXT,
            run_complete_time TEXT,
            raw_orderbook_hash_transition_delta_ms INTEGER,
            contributes_to_target_extrema INTEGER,
            forecast_window_attribution_status TEXT,
            forecast_window_start_utc TEXT,
            forecast_window_end_utc TEXT,
            available_at TEXT NOT NULL,
            authority TEXT,
            causality_status TEXT,
            boundary_ambiguous INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 VALUES (
            '1',
            'Chicago',
            '2026-05-25',
            'high',
            ?,
            ?,
            ?,
            'pcal-model-1',
            'platt-v2',
            'VERIFIED',
            '2026-05-24T08:09:00+00:00',
            'ecmwf_open_data',
            'run-1',
            'degF',
            'F',
            'ecmwf_open_data',
            'ensemble_snapshots_v2_db_reader',
            'run-1',
            'ecmwf_open_data',
            '2026-05-24T00:00:00+00:00',
            '2026-05-24T07:00:00+00:00',
            '2026-05-24T08:10:00+00:00',
            '2026-05-24T00:00:00+00:00',
            '2026-05-25',
            '2026-05-24T08:10:00+00:00',
            'hash-manifest',
            32.0,
            'ecmwf_opendata_mx2t3_local_calendar_day_max_v1',
            '2026-05-25T05:00:00+00:00',
            32.0,
            '2026-05-24T07:10:00+00:00',
            '2026-05-24T08:05:00+00:00',
            50,
            1,
            'FULLY_INSIDE_TARGET_LOCAL_DAY',
            '2026-05-25T05:00:00+00:00',
            '2026-05-26T05:00:00+00:00',
            '2026-05-24T08:10:00+00:00',
            'VERIFIED',
            'OK',
            0
        )
        """,
        (
            json.dumps([70.5] * 41 + [71.5] * 10, separators=(",", ":")),
            json.dumps([0.80, 0.20], separators=(",", ":")),
            json.dumps([0.88, 0.12], separators=(",", ":")),
        ),
    )
    _insert_forecast_reader_authority(conn)
    conn.execute(
        """
        CREATE TABLE probability_trace_fact (
            trace_id TEXT PRIMARY KEY,
            decision_id TEXT,
            decision_snapshot_id TEXT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT,
            p_posterior REAL,
            recorded_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE selection_family_fact (
            family_id TEXT PRIMARY KEY,
            decision_snapshot_id TEXT,
            city TEXT,
            target_date TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE selection_hypothesis_fact (
            hypothesis_id TEXT PRIMARY KEY,
            family_id TEXT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT,
            p_value REAL,
            ci_lower REAL,
            passed_prefilter INTEGER,
            recorded_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO selection_family_fact VALUES ('canonical-family-1', '1', 'Chicago', '2026-05-25')"
    )
    probability_rows = []
    hypothesis_rows = []
    for index in range(1, condition_count + 1):
        label = f"{70 + index - 1}-{71 + index - 1}°F"
        yes_q = 0.80 if index == 1 else 0.20
        no_q = 1.0 - yes_q
        probability_rows.extend(
            [
                (f"trace-yes-{index}", f"decision-yes-{index}", "1", "Chicago", "2026-05-25", label, "buy_yes", yes_q, "2026-05-24T08:12:00+00:00"),
                (f"trace-no-{index}", f"decision-no-{index}", "1", "Chicago", "2026-05-25", label, "buy_no", no_q, "2026-05-24T08:12:00+00:00"),
            ]
        )
        hypothesis_rows.extend(
            [
                (f"hyp-yes-{index}", "canonical-family-1", "Chicago", "2026-05-25", label, "buy_yes", 0.001 if index == 1 else 0.80, 0.72 if index == 1 else 0.12, 1, "2026-05-24T08:12:00+00:00"),
                (f"hyp-no-{index}", "canonical-family-1", "Chicago", "2026-05-25", label, "buy_no", 0.90 if index == 1 else 0.85, 0.12 if index == 1 else 0.72, 1, "2026-05-24T08:12:00+00:00"),
            ]
        )
    conn.executemany(
        "INSERT INTO probability_trace_fact VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        probability_rows,
    )
    conn.executemany(
        "INSERT INTO selection_hypothesis_fact VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        hypothesis_rows,
    )
    return conn


def _insert_forecast_reader_authority(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            track TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            ingest_mode TEXT NOT NULL,
            origin_mode TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            source_issue_time TEXT,
            source_release_time TEXT,
            source_available_at TEXT,
            fetch_started_at TEXT,
            fetch_finished_at TEXT,
            captured_at TEXT,
            imported_at TEXT,
            valid_time_start TEXT,
            valid_time_end TEXT,
            target_local_date TEXT,
            city_id TEXT,
            city_timezone TEXT,
            temperature_metric TEXT,
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            expected_members INTEGER,
            observed_members INTEGER,
            expected_steps_json TEXT NOT NULL DEFAULT '[]',
            observed_steps_json TEXT NOT NULL DEFAULT '[]',
            expected_count INTEGER,
            observed_count INTEGER,
            completeness_status TEXT NOT NULL,
            partial_run INTEGER NOT NULL DEFAULT 0,
            raw_payload_hash TEXT,
            manifest_hash TEXT,
            status TEXT NOT NULL,
            reason_code TEXT,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_transport TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            track TEXT NOT NULL,
            city_id TEXT NOT NULL,
            city TEXT NOT NULL,
            city_timezone TEXT NOT NULL,
            target_local_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            physical_quantity TEXT NOT NULL,
            observation_field TEXT NOT NULL,
            data_version TEXT NOT NULL,
            expected_members INTEGER NOT NULL,
            observed_members INTEGER NOT NULL,
            expected_steps_json TEXT NOT NULL,
            observed_steps_json TEXT NOT NULL,
            snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
            target_window_start_utc TEXT NOT NULL,
            target_window_end_utc TEXT NOT NULL,
            completeness_status TEXT NOT NULL,
            readiness_status TEXT NOT NULL,
            reason_code TEXT,
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("DELETE FROM source_run WHERE source_run_id = 'run-1'")
    conn.execute("DELETE FROM source_run_coverage WHERE coverage_id = 'coverage-1'")
    conn.execute(
        """
        INSERT INTO source_run (
            source_run_id, source_id, track, release_calendar_key, ingest_mode, origin_mode,
            source_cycle_time, source_issue_time, source_release_time, source_available_at,
            fetch_started_at, fetch_finished_at, captured_at, imported_at,
            valid_time_start, valid_time_end, target_local_date, city_id, city_timezone,
            temperature_metric, physical_quantity, observation_field, data_version,
            expected_members, observed_members, expected_steps_json, observed_steps_json,
            expected_count, observed_count, completeness_status, partial_run,
            raw_payload_hash, manifest_hash, status, reason_code
        ) VALUES (
            'run-1', 'ecmwf_open_data', 'operational', 'ecmwf_open_data',
            'SCHEDULED_LIVE', 'SCHEDULED_LIVE',
            '2026-05-24T00:00:00+00:00', '2026-05-24T00:00:00+00:00',
            '2026-05-24T07:00:00+00:00', '2026-05-24T08:10:00+00:00',
            '2026-05-24T07:10:00+00:00', '2026-05-24T08:05:00+00:00',
            '2026-05-24T08:10:00+00:00', '2026-05-24T08:10:00+00:00',
            '2026-05-25T05:00:00+00:00', '2026-05-26T05:00:00+00:00',
            '2026-05-25', 'Chicago', 'America/Chicago', 'high',
            'temperature', 'high_temp', 'ecmwf_opendata_mx2t3_local_calendar_day_max_v1',
            51, 51, '[0,3,6]', '[0,3,6]', 3, 3,
            'COMPLETE', 0, 'hash-raw', 'hash-manifest', 'SUCCESS', NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO source_run_coverage (
            coverage_id, source_run_id, source_id, source_transport, release_calendar_key, track,
            city_id, city, city_timezone, target_local_date, temperature_metric,
            physical_quantity, observation_field, data_version, expected_members, observed_members,
            expected_steps_json, observed_steps_json, snapshot_ids_json, target_window_start_utc,
            target_window_end_utc, completeness_status, readiness_status, reason_code,
            computed_at, expires_at
        ) VALUES (
            'coverage-1', 'run-1', 'ecmwf_open_data', 'ensemble_snapshots_v2_db_reader',
            'ecmwf_open_data', 'operational', 'Chicago', 'Chicago', 'America/Chicago',
            '2026-05-25', 'high', 'temperature', 'high_temp',
            'ecmwf_opendata_mx2t3_local_calendar_day_max_v1', 51, 51, '[0,3,6]', '[0,3,6]',
            '["1"]', '2026-05-25T05:00:00+00:00', '2026-05-26T05:00:00+00:00',
            'COMPLETE', 'LIVE_ELIGIBLE', NULL, '2026-05-24T08:10:00+00:00',
            '2026-05-25T00:00:00+00:00'
        )
        """
    )


def _calibration_conn_with_platt_model() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _insert_platt_model(conn)
    return conn


def _insert_platt_model(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS platt_models_v2 (
            model_key TEXT PRIMARY KEY,
            temperature_metric TEXT NOT NULL,
            cluster TEXT NOT NULL,
            season TEXT NOT NULL,
            data_version TEXT NOT NULL,
            input_space TEXT NOT NULL,
            param_A REAL NOT NULL,
            param_B REAL NOT NULL,
            param_C REAL NOT NULL,
            bootstrap_params_json TEXT NOT NULL,
            n_samples INTEGER NOT NULL,
            brier_insample REAL,
            fitted_at TEXT NOT NULL,
            is_active INTEGER NOT NULL,
            authority TEXT NOT NULL,
            cycle TEXT NOT NULL,
            source_id TEXT NOT NULL,
            horizon_profile TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute("DELETE FROM platt_models_v2 WHERE model_key = 'platt-world-1'")
    conn.execute(
        """
        INSERT INTO platt_models_v2 VALUES (
            'platt-world-1', 'high', 'Chicago', 'MAM',
            'tigge_mx2t6_local_calendar_day_max_v1',
            'width_normalized_density',
            1.15, 0.01, 0.02, ?,
            60, 0.12, '2026-05-01T00:00:00+00:00',
            1, 'VERIFIED', '00', 'tigge_mars', 'full',
            '2026-05-01T00:00:00+00:00'
        )
        """,
        (json.dumps([[1.15, 0.01, 0.02]], separators=(",", ":")),),
    )


def _receipt(event, conn: sqlite3.Connection, **kwargs):
    forecast_conn = kwargs.pop("forecast_conn", conn)
    topology_conn = kwargs.pop("topology_conn", forecast_conn)
    calibration_conn = kwargs.pop("calibration_conn", kwargs.pop("world_conn", conn))
    return build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        forecast_conn=forecast_conn,
        topology_conn=topology_conn,
        calibration_conn=calibration_conn,
        get_current_level=kwargs.pop("get_current_level", lambda: RiskLevel.GREEN),
        bankroll_usd_provider=kwargs.pop("bankroll_usd_provider", lambda: 100.0),
        **kwargs,
    )


def test_reactor_never_imports_venue_adapter():
    tree = ast.parse(Path("src/events/reactor.py").read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert all("venue_adapter" not in imported for imported in imports)


def test_engine_adapter_has_no_cycle_or_executor_boundary():
    source = Path("src/engine/event_reactor_adapter.py").read_text()
    assert "venue_adapter" not in source
    assert "execute_final_intent" not in source
    assert "run_cycle" not in source
    assert "submit_existing_cycle_for_event" not in source
    assert "edli_submit_accepted" not in source
    assert "final_intents_built" not in source


def test_adapter_source_truth_allows_complete_forecast_only():
    complete = _forecast_event("COMPLETE")
    partial_payload = json.loads(complete.payload_json)
    partial_payload["completeness_status"] = "PARTIAL_ALLOWED"
    partial = replace(complete, payload_json=json.dumps(partial_payload, sort_keys=True, separators=(",", ":")))

    assert edli_source_truth_gate(complete) is True
    assert edli_source_truth_gate(partial) is False


def test_adapter_trade_score_gate_treats_trigger_events_as_hydration_inputs():
    event = _forecast_event()
    payload = json.loads(event.payload_json)
    payload.update(
        {
            "p_fill_lcb": 0.5,
            "q_5pct": 0.62,
            "q_posterior": 0.64,
            "c_95pct": 0.55,
            "c_stress": 0.56,
            "lambda_edge": 0.01,
            "lambda_stress": 0.01,
        }
    )
    positive = replace(event, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))
    payload["c_95pct"] = 0.70
    negative = replace(event, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))

    assert edli_trade_score_gate(positive) is True
    assert edli_trade_score_gate(negative) is True
    assert edli_trade_score_gate(event) is True


def test_runtime_receipt_uses_event_bound_final_intent_contract():
    event = _bound_forecast_event()
    receipt = _receipt(event, _trade_conn_with_snapshot())

    assert receipt.submitted is True
    assert receipt.event_id == event.event_id
    assert receipt.causal_snapshot_id == event.causal_snapshot_id
    assert receipt.trade_score_positive is True
    assert receipt.trade_score is not None
    assert receipt.trade_score > 0
    assert receipt.q_live is not None
    assert receipt.q_live > 0.85
    assert receipt.c_fee_adjusted is not None
    assert receipt.p_fill_lcb is not None
    assert 0.0 < receipt.p_fill_lcb < 1.0
    assert receipt.family_complete is True
    assert receipt.fdr_pass is True
    assert receipt.fdr_hypothesis_count == 4
    assert receipt.kelly_execution_price_type == "ExecutionPrice"
    assert receipt.kelly_price_fee_deducted is True
    assert receipt.kelly_size_usd > 0
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_forecast_trigger_event_without_q_or_token_fields_builds_no_submit_receipt():
    event = _forecast_event()
    receipt = _receipt(event, _trade_conn_with_snapshot())

    assert receipt.submitted is True
    assert receipt.token_id == "yes-1"
    assert receipt.q_live is not None
    assert receipt.q_live > 0.85
    assert receipt.trade_score is not None
    assert receipt.fdr_hypothesis_count == 4
    assert receipt.kelly_execution_price_type == "ExecutionPrice"
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_family_candidates_use_market_event_range_bounds_not_payload_default():
    event = _forecast_event()
    receipt = _receipt(event, _trade_conn_with_snapshot())

    assert receipt.bin_label == "70-71°F"
    assert receipt.bin_label != "0-1°F"


def test_selected_snapshot_row_not_first_still_binds_matching_candidate():
    event = _bound_forecast_event(token_id="yes-2")
    receipt = _receipt(event, _trade_conn_with_snapshot())

    assert receipt.condition_id == "condition-2"
    assert receipt.token_id == "yes-2"
    assert receipt.bin_label == "71-72°F"


def test_runtime_receipt_uses_selected_no_snapshot_not_yes_side_ask():
    event = _bound_forecast_event(token_id="no-1")
    receipt = _receipt(event, _trade_conn_with_snapshot(selected_ask="0.10", no_selected_ask="0.80"))

    assert receipt.submitted is False
    assert receipt.token_id == "no-1"
    assert receipt.executable_snapshot_id == "snapshot-exec-1-no"
    assert receipt.c_fee_adjusted is not None
    assert receipt.c_fee_adjusted >= 0.80
    assert receipt.reason == "TRADE_SCORE_NON_POSITIVE"


def test_runtime_receipt_rejects_selected_no_when_only_yes_side_snapshot_exists():
    event = _bound_forecast_event(token_id="no-1")
    conn = _trade_conn_with_snapshot()
    conn.execute("DELETE FROM executable_market_snapshots WHERE selected_outcome_token_id = 'no-1'")
    conn.execute(
        """
        UPDATE executable_market_snapshots
        SET orderbook_depth_json = ?
        WHERE selected_outcome_token_id = 'yes-1'
        """,
        (json.dumps({"YES": {"asks": [{"price": "0.40", "size": "100"}], "bids": []}}, separators=(",", ":")),),
    )

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert receipt.reason.startswith("EXECUTABLE_NATIVE_ASK_MISSING")


def test_runtime_receipt_rejects_when_family_topology_has_missing_sibling_snapshot():
    event = _bound_forecast_event(fdr_condition_count=3)
    receipt = _receipt(event, _trade_conn_with_snapshot(condition_count=3, snapshot_condition_count=2))

    assert receipt.submitted is False
    assert receipt.reason.startswith("FDR_FULL_FAMILY_PROOF_MISSING")
    assert receipt.family_complete is False


def test_runtime_receipt_generates_fdr_from_family_not_event_payload():
    event = _bound_forecast_event()
    payload = json.loads(event.payload_json)
    payload.pop("fdr_hypotheses", None)
    event = replace(event, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))

    receipt = _receipt(event, _trade_conn_with_snapshot())

    assert receipt.fdr_hypothesis_count == 4
    assert receipt.reason != "FDR_FULL_FAMILY_PROOF_MISSING"


def test_forecast_receipt_does_not_require_old_probability_or_selection_facts():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("DROP TABLE probability_trace_fact")
    conn.execute("DROP TABLE selection_hypothesis_fact")
    conn.execute("DROP TABLE selection_family_fact")

    receipt = _receipt(event, conn)

    assert receipt.submitted is True
    assert receipt.q_live is not None
    assert receipt.q_live > 0.85
    assert receipt.fdr_pass is True
    assert receipt.fdr_hypothesis_count == 4


def test_forecast_receipt_uses_separate_forecast_authority_connection():
    event = _bound_forecast_event()
    trade_conn = _trade_conn_with_snapshot()
    forecast_conn = _trade_conn_with_snapshot()
    forecast_conn.execute("DROP TABLE executable_market_snapshots")
    trade_conn.execute("DROP TABLE ensemble_snapshots_v2")
    trade_conn.execute("DROP TABLE market_events_v2")
    trade_conn.execute("DROP TABLE source_run")
    trade_conn.execute("DROP TABLE source_run_coverage")

    receipt = _receipt(event, trade_conn, forecast_conn=forecast_conn, topology_conn=forecast_conn)

    assert receipt.submitted is True
    assert receipt.q_live is not None
    assert receipt.q_live > 0.85
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_executable_snapshot_gate_uses_forecast_topology_authority_connection():
    event = _bound_forecast_event()
    trade_conn = _trade_conn_with_snapshot()
    forecast_conn = _trade_conn_with_snapshot()
    forecast_conn.execute("DROP TABLE executable_market_snapshots")
    trade_conn.execute("DROP TABLE market_events_v2")
    gate = executable_snapshot_gate_from_trade_conn(trade_conn, topology_conn=forecast_conn)

    assert gate(event) is True


def test_executable_snapshot_gate_requires_topology_authority_connection():
    event = _bound_forecast_event()
    gate = executable_snapshot_gate_from_trade_conn(_trade_conn_with_snapshot())

    assert gate(event) is False


def test_receipt_requires_explicit_forecast_and_topology_authority_connections():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()

    missing_forecast = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        topology_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )
    missing_topology = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        forecast_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )
    missing_calibration = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        forecast_conn=conn,
        topology_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert missing_forecast.submitted is False
    assert missing_forecast.reason == "FORECAST_AUTHORITY_CONNECTION_MISSING"
    assert missing_topology.submitted is False
    assert missing_topology.reason == "TOPOLOGY_AUTHORITY_CONNECTION_MISSING"
    assert missing_calibration.submitted is False
    assert missing_calibration.reason == "CALIBRATION_AUTHORITY_CONNECTION_MISSING"


def test_missing_calibration_authority_blocks_receipt():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots_v2 SET p_cal_json = NULL")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:CALIBRATION_AUTHORITY_MISSING")


def test_receipt_uses_world_calibration_authority_not_forecast_conn():
    event = _bound_forecast_event()
    trade_conn = _trade_conn_with_snapshot()
    forecast_conn = _trade_conn_with_snapshot()
    calibration_conn = _calibration_conn_with_platt_model()
    forecast_conn.execute("DROP TABLE executable_market_snapshots")
    trade_conn.execute("DROP TABLE ensemble_snapshots_v2")
    trade_conn.execute("DROP TABLE market_events_v2")
    trade_conn.execute("DROP TABLE source_run")
    trade_conn.execute("DROP TABLE source_run_coverage")
    forecast_conn.execute("UPDATE ensemble_snapshots_v2 SET p_cal_json = NULL")

    receipt = _receipt(
        event,
        trade_conn,
        forecast_conn=forecast_conn,
        topology_conn=forecast_conn,
        calibration_conn=calibration_conn,
    )

    assert receipt.submitted is True
    assert receipt.q_live is not None
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_forecast_conn_fake_platt_model_is_not_calibration_authority():
    event = _bound_forecast_event()
    trade_conn = _trade_conn_with_snapshot()
    forecast_conn = _trade_conn_with_snapshot()
    forecast_conn.execute("DROP TABLE executable_market_snapshots")
    trade_conn.execute("DROP TABLE ensemble_snapshots_v2")
    trade_conn.execute("DROP TABLE market_events_v2")
    trade_conn.execute("DROP TABLE source_run")
    trade_conn.execute("DROP TABLE source_run_coverage")
    forecast_conn.execute("UPDATE ensemble_snapshots_v2 SET p_cal_json = NULL")
    _insert_platt_model(forecast_conn)

    receipt = _receipt(
        event,
        trade_conn,
        forecast_conn=forecast_conn,
        topology_conn=forecast_conn,
        calibration_conn=sqlite3.connect(":memory:"),
    )

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:CALIBRATION_AUTHORITY_MISSING")


def test_p_cal_json_without_authority_fields_blocks():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots_v2 SET p_cal_authority = NULL")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert "CALIBRATION_AUTHORITY_MISSING:p_cal_json authority missing" in receipt.reason


def test_p_cal_json_available_after_event_blocks():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots_v2 SET p_cal_available_at = '2026-05-24T08:11:00+00:00'")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert "CALIBRATION_AUTHORITY_MISSING:p_cal_json not available" in receipt.reason


def test_receipt_revalidates_source_run_coverage_after_event_emit():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE source_run_coverage SET readiness_status = 'BLOCKED'")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert "FORECAST_READER_REVALIDATION_FAILED:readiness_BLOCKED" in receipt.reason


def test_receipt_revalidates_source_run_coverage_snapshot_ids():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE source_run_coverage SET snapshot_ids_json = '[\"other-snapshot\"]'")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert "FORECAST_READER_REVALIDATION_FAILED:coverage_snapshot_mismatch" in receipt.reason


def test_receipt_revalidates_executable_forecast_reader_authority(monkeypatch):
    from types import SimpleNamespace

    from src.data import executable_forecast_reader

    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()

    monkeypatch.setattr(
        executable_forecast_reader,
        "read_executable_forecast_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(ok=False, snapshot=None, reason_code="READER_TEST_BLOCK"),
    )

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert "FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:READER_TEST_BLOCK" in receipt.reason


def test_top_ask_without_depth_does_not_create_fillable_quote():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot(selected_ask="0.40")
    conn.execute(
        """
        UPDATE executable_market_snapshots
        SET orderbook_depth_json = '{}'
        WHERE condition_id = 'condition-1'
        """
    )

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert receipt.reason.startswith("EXECUTABLE_NATIVE_ASK_MISSING")
    assert receipt.native_quote_available is False


def test_real_snapshot_depth_at_best_ask_authorizes_selected_token_cost():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot(selected_ask="0.40")
    conn.execute("ALTER TABLE executable_market_snapshots ADD COLUMN depth_at_best_ask TEXT")
    conn.execute(
        """
        UPDATE executable_market_snapshots
        SET orderbook_depth_json = '{}',
            depth_at_best_ask = '100'
        """
    )

    receipt = _receipt(event, conn)

    assert receipt.submitted is True
    assert receipt.c_fee_adjusted == 0.40
    assert receipt.native_quote_available is True
    assert receipt.p_fill_lcb == 0.05


def test_no_submit_default_bankroll_path_does_not_live_fetch_wallet(monkeypatch):
    from src.runtime import bankroll_provider

    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()

    def _explode_current(**_kwargs):
        raise AssertionError("no-submit proof must not live-fetch wallet bankroll")

    monkeypatch.setattr(bankroll_provider, "current", _explode_current)
    monkeypatch.setattr(bankroll_provider, "cached", lambda **_kwargs: None)

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        forecast_conn=conn,
        topology_conn=conn,
        calibration_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert receipt.submitted is False
    assert receipt.reason == "KELLY_PROOF_MISSING:bankroll_provider_unavailable"


def test_forecast_receipt_uses_attached_forecasts_market_topology():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("ALTER TABLE market_events_v2 RENAME TO attached_market_events_v2")
    conn.execute("ATTACH DATABASE ':memory:' AS forecasts")
    conn.execute("CREATE TABLE forecasts.ensemble_snapshots_v2 AS SELECT * FROM ensemble_snapshots_v2")
    conn.execute("CREATE TABLE forecasts.source_run AS SELECT * FROM source_run")
    conn.execute("CREATE TABLE forecasts.source_run_coverage AS SELECT * FROM source_run_coverage")
    conn.execute(
        """
        CREATE TABLE forecasts.market_events_v2 (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            outcome TEXT,
            condition_id TEXT,
            token_id TEXT,
            market_slug TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO forecasts.market_events_v2 VALUES (
            'Chicago', '2026-05-25', 'high', ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            ("70-71°F", "condition-1", "yes-1", "chicago-high-1", "70-71°F", 70.0, 71.0),
            ("71-72°F", "condition-2", "yes-2", "chicago-high-2", "71-72°F", 71.0, 72.0),
        ],
    )

    receipt = _receipt(event, conn, forecast_conn=conn, topology_conn=conn)

    assert receipt.submitted is True
    assert receipt.fdr_hypothesis_count == 4


def test_forecast_receipt_rejects_source_snapshot_available_after_event_available_time():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots_v2 SET available_at = '2026-05-24T08:11:00+00:00'")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:causal forecast snapshot missing")


def test_forecast_receipt_requires_exact_causal_snapshot_from_source_data():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots_v2 SET snapshot_id = 'other-snapshot'")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:causal forecast snapshot missing")


def test_forecast_receipt_requires_metric_match_in_source_snapshot():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots_v2 SET temperature_metric = 'low'")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:causal forecast snapshot missing")


def test_day0_receipt_uses_latest_forecast_source_and_absorbing_boundary_not_old_facts():
    event = _day0_event(token_id="yes-2")
    conn = _trade_conn_with_snapshot()
    conn.execute("DROP TABLE probability_trace_fact")
    conn.execute("DROP TABLE selection_hypothesis_fact")
    conn.execute("DROP TABLE selection_family_fact")

    receipt = _receipt(event, conn)

    assert receipt.submitted is True
    assert receipt.condition_id == "condition-2"
    assert receipt.token_id == "yes-2"
    assert receipt.q_live is not None
    assert receipt.q_live > 0.99
    assert receipt.fdr_hypothesis_count == 4
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_runtime_receipt_rejects_missing_native_ask_instead_of_defaulting_midpoint():
    event = _bound_forecast_event()
    receipt = _receipt(event, _trade_conn_with_snapshot(selected_ask=""))

    assert receipt.submitted is False
    assert receipt.reason.startswith("EXECUTABLE_NATIVE_ASK_MISSING")


def test_runtime_receipt_uses_runtime_kelly_authority_not_event_payload():
    event = _bound_forecast_event()
    payload = json.loads(event.payload_json)
    payload["bankroll_usd"] = 0
    payload["kelly_multiplier"] = 0
    event = replace(event, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))

    receipt = _receipt(event, _trade_conn_with_snapshot())

    assert receipt.kelly_pass is True
    assert receipt.kelly_size_usd > 0
    assert receipt.reason != "KELLY_PROOF_MISSING"
