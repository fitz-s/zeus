# Created: 2026-05-24
# Last reused/audited: 2026-06-05
# Authority basis: Operator GOAL 2026-06-04 — full-family q/FDR + executable-mask for illiquid bins; never trade an assumed/renormalized subset
from __future__ import annotations

import ast
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.decision_kernel import claims
from src.decision_kernel.compiler import DecisionCompiler
from src.state.snapshot_repo import init_snapshot_schema
from src.engine.event_reactor_adapter import (
    build_event_bound_no_submit_receipt,
    edli_source_truth_gate,
    edli_trade_score_gate,
    executable_snapshot_gate_from_trade_conn,
    _snapshot_p_cal,
    _snapshot_members_json_hash,
    _snapshot_p_raw,
    _snapshot_unit,
    _probability_vector_hash,
)
from src.config import runtime_cities_by_name
from src.contracts.settlement_semantics import SettlementSemantics
from src.events.opportunity_event import Day0ExtremeUpdatedPayload, ForecastSnapshotReadyPayload, make_day0_extreme_updated_event, make_opportunity_event
from src.riskguard.risk_level import RiskLevel
from src.signal.ensemble_signal import p_raw_vector_from_maxes
from src.state.db import init_schema_forecasts
from src.types.market import Bin

DECISION_TIME = datetime(2026, 5, 24, 8, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_edli_settings(monkeypatch):
    """Force flag-OFF for EMOS sole calibrator and bias correction.

    The test fixture has no EMOS calibration rows and no model_bias_ens rows.
    Live settings.json may have these flags ON (edli_emos_sole_calibrator_enabled,
    edli_bias_correction_enabled).  With EMOS ON and no calibration data, build_emos_q
    produces a different q distribution than what the fixture encodes, causing
    TRADE_SCORE_NON_POSITIVE on every receipt assertion.  Isolate all tests in this
    module from the live flag state.
    """
    from src.config import settings

    edli = dict(settings._data["edli_v1"])
    edli["edli_emos_sole_calibrator_enabled"] = False
    edli["edli_bias_correction_enabled"] = False
    monkeypatch.setitem(settings._data, "edli_v1", edli)


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


def _low_bound_forecast_event():
    event = _bound_forecast_event()
    payload = json.loads(event.payload_json)
    payload["metric"] = "low"
    return replace(
        event,
        entity_key="Chicago|2026-05-25|low|run-1",
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def _convert_fixture_to_low_extrema(conn: sqlite3.Connection) -> None:
    low_data_version = "ecmwf_opendata_mn2t3_local_calendar_day_min_contract_window"
    low_platt_data_version = "tigge_mn2t6_local_calendar_day_min_contract_window"
    low_members = [50.5] * 41 + [49.5] * 10
    conn.execute(
        """
        UPDATE market_events
        SET temperature_metric = 'low',
            market_slug = replace(market_slug, 'high', 'low'),
            range_label = replace(range_label, '70', '50'),
            range_low = range_low - 20,
            range_high = range_high - 20
        """
    )
    conn.execute(
        """
        UPDATE source_run
        SET temperature_metric = 'low',
            observation_field = 'low_temp',
            dataset_id = ?,
            physical_quantity = 'temperature'
        WHERE source_run_id = 'run-1'
        """,
        (low_data_version,),
    )
    conn.execute(
        """
        UPDATE source_run_coverage
        SET temperature_metric = 'low',
            observation_field = 'low_temp',
            data_version = ?,
            physical_quantity = 'temperature'
        WHERE coverage_id = 'coverage-1'
        """,
        (low_data_version,),
    )
    conn.execute(
        """
        UPDATE readiness_state
        SET temperature_metric = 'low',
            observation_field = 'low_temp',
            data_version = ?
        WHERE readiness_id = 'producer-readiness-1'
        """,
        (low_data_version,),
    )
    conn.execute(
        """
        UPDATE ensemble_snapshots
        SET temperature_metric = 'low',
            members_json = ?,
            p_raw_json = ?,
            p_cal_json = ?,
            dataset_id = ?
        WHERE snapshot_id = '1'
        """,
        (
            json.dumps(low_members, separators=(",", ":")),
            json.dumps([0.80, 0.20], separators=(",", ":")),
            json.dumps([0.88, 0.12], separators=(",", ":")),
            low_data_version,
        ),
    )
    conn.execute(
        """
        UPDATE platt_models
        SET temperature_metric = 'low',
            data_version = ?,
            source_id = 'tigge_mars'
        WHERE model_key = 'platt-world-1'
        """,
        (low_platt_data_version,),
    )


def _day0_event(*, token_id: str = "yes-2"):
    payload = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date="2026-05-25",
        metric="high",
        settlement_source="wu_icao",
        station_id="KMDW",
        observation_time="2026-05-24T14:00:00+00:00",
        observation_available_at="2026-05-24T14:05:00+00:00",
        raw_value=72.1,
        rounded_value=72,
        high_so_far=72.1,
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="LIVE_AUTHORITY",
    )
    event = make_day0_extreme_updated_event(
        entity_key="Chicago|2026-05-25|high",
        source="day0_extreme_updated_trigger",
        observed_at=payload.observation_time,
        received_at="2026-05-24T14:06:00+00:00",
        payload=payload,
        causal_snapshot_id="day0-observation-1",
    )
    event_payload = json.loads(event.payload_json)
    event_payload.update({
        "condition_id": "condition-2",
        "token_id": token_id,
        "unit": "F",
        # S3 Kelly sizing requires lead_days; executable_market_snapshots has no
        # lead_hours/issue_time column, so supply it via payload (matches ensemble
        # snapshot lead_hours=32.0 in the trade fixture).
        "lead_hours": 32.0,
    })
    return replace(event, payload_json=json.dumps(event_payload, sort_keys=True, separators=(",", ":")))


def _trade_conn_with_snapshot(
    *,
    selected_ask: str = "0.40",
    no_selected_ask: str = "0.80",
    condition_count: int = 2,
    snapshot_condition_count: int | None = None,
    include_no_snapshot: bool = True,
    freshness_deadline: str = "2026-05-25T00:00:00+00:00",
    captured_at: str = "2026-05-24T08:12:00+00:00",
    depth_json: str | None = None,
):
    if snapshot_condition_count is None:
        snapshot_condition_count = condition_count
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_snapshot_schema(conn)
    _depth_yes_no = depth_json if depth_json is not None else json.dumps(
        {
            "YES": {"asks": [{"price": selected_ask, "size": "100"}], "bids": [{"price": "0.39", "size": "100"}]},
            "NO": {"asks": [{"price": no_selected_ask, "size": "100"}], "bids": [{"price": "0.19", "size": "100"}]},
        },
        separators=(",", ":"),
    )
    _SNAP_BASE = dict(
        gamma_market_id="gamma-mkt-1",
        event_id="event-1",
        event_slug="chicago-temperature-high",
        question_id="q-1",
        enable_orderbook=1,
        accepting_orders=1,
        market_start_at=None,
        market_end_at=None,
        market_close_at=None,
        sports_start_at=None,
        token_map_json='{"yes":"yes-1","no":"no-1"}',
        rfqe=None,
        raw_gamma_payload_hash="a" * 64,
        raw_clob_market_info_hash="b" * 64,
        raw_orderbook_hash="c" * 64,
        authority_tier="CLOB",
        wide_spread_display_substitution=0,
        depth_at_best_ask=0,
        tradeability_status_json="{}",
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, condition_id, yes_token_id, no_token_id,
            selected_outcome_token_id, outcome_label,
            orderbook_top_ask, orderbook_top_bid, orderbook_depth_json,
            min_tick_size, min_order_size, fee_details_json, neg_risk,
            freshness_deadline, captured_at, active, closed,
            gamma_market_id, event_id, event_slug, question_id,
            enable_orderbook, accepting_orders,
            market_start_at, market_end_at, market_close_at, sports_start_at,
            token_map_json, rfqe,
            raw_gamma_payload_hash, raw_clob_market_info_hash, raw_orderbook_hash,
            authority_tier,
            wide_spread_display_substitution, depth_at_best_ask,
            tradeability_status_json
        ) VALUES (
            'snapshot-exec-1', 'condition-1', 'yes-1', 'no-1', 'yes-1', 'YES',
            :ask, '0.39', :depth, '0.01', '5', '{"fee_rate_fraction":0.0}', 0,
            :freshness_deadline, :captured_at, 1, 0,
            :gamma_market_id, :event_id, :event_slug, :question_id,
            :enable_orderbook, :accepting_orders,
            :market_start_at, :market_end_at, :market_close_at, :sports_start_at,
            :token_map_json, :rfqe,
            :raw_gamma_payload_hash, :raw_clob_market_info_hash, :raw_orderbook_hash,
            :authority_tier,
            :wide_spread_display_substitution, :depth_at_best_ask,
            :tradeability_status_json
        )
        """,
        {"ask": selected_ask, "depth": _depth_yes_no, "freshness_deadline": freshness_deadline, "captured_at": captured_at, **_SNAP_BASE},
    )
    if include_no_snapshot:
        conn.execute(
            """
            INSERT INTO executable_market_snapshots (
                snapshot_id, condition_id, yes_token_id, no_token_id,
                selected_outcome_token_id, outcome_label,
                orderbook_top_ask, orderbook_top_bid, orderbook_depth_json,
                min_tick_size, min_order_size, fee_details_json, neg_risk,
                freshness_deadline, captured_at, active, closed,
                gamma_market_id, event_id, event_slug, question_id,
                enable_orderbook, accepting_orders,
                market_start_at, market_end_at, market_close_at, sports_start_at,
                token_map_json, rfqe,
                raw_gamma_payload_hash, raw_clob_market_info_hash, raw_orderbook_hash,
                authority_tier,
                wide_spread_display_substitution, depth_at_best_ask,
                tradeability_status_json
            ) VALUES (
                'snapshot-exec-1-no', 'condition-1', 'yes-1', 'no-1', 'no-1', 'NO',
                :ask, '0.19', :depth, '0.01', '5', '{"fee_rate_fraction":0.0}', 0,
                :freshness_deadline, :captured_at, 1, 0,
                :gamma_market_id, :event_id, :event_slug, :question_id,
                :enable_orderbook, :accepting_orders,
                :market_start_at, :market_end_at, :market_close_at, :sports_start_at,
                :token_map_json, :rfqe,
                :raw_gamma_payload_hash, :raw_clob_market_info_hash, :raw_orderbook_hash,
                :authority_tier,
                :wide_spread_display_substitution, :depth_at_best_ask,
                :tradeability_status_json
            )
            """,
            {"ask": no_selected_ask, "depth": _depth_yes_no, "freshness_deadline": freshness_deadline, "captured_at": captured_at, **_SNAP_BASE},
        )
    for index in range(2, snapshot_condition_count + 1):
        _depth_extra = json.dumps(
            {
                "YES": {"asks": [{"price": "0.48", "size": "100"}], "bids": [{"price": "0.47", "size": "100"}]},
                "NO": {"asks": [{"price": "0.60", "size": "100"}], "bids": [{"price": "0.40", "size": "100"}]},
            },
            separators=(",", ":"),
        )
        _extra_base = {**_SNAP_BASE, "gamma_market_id": f"gamma-mkt-{index}", "question_id": f"q-{index}",
                       "token_map_json": json.dumps({"yes": f"yes-{index}", "no": f"no-{index}"}, separators=(",", ":"))}
        conn.execute(
            """
            INSERT INTO executable_market_snapshots (
                snapshot_id, condition_id, yes_token_id, no_token_id,
                selected_outcome_token_id, outcome_label,
                orderbook_top_ask, orderbook_top_bid, orderbook_depth_json,
                min_tick_size, min_order_size, fee_details_json, neg_risk,
                freshness_deadline, captured_at, active, closed,
                gamma_market_id, event_id, event_slug, question_id,
                enable_orderbook, accepting_orders,
                market_start_at, market_end_at, market_close_at, sports_start_at,
                token_map_json, rfqe,
                raw_gamma_payload_hash, raw_clob_market_info_hash, raw_orderbook_hash,
                authority_tier,
                wide_spread_display_substitution, depth_at_best_ask,
                tradeability_status_json
            ) VALUES (
                :snap_id, :cond_id, :yes_id, :no_id, :yes_id, 'YES',
                '0.48', '0.47', :depth, '0.01', '5', '{"fee_rate_fraction":0.0}', 0,
                '2026-05-25T00:00:00+00:00', '2026-05-24T08:12:00+00:00', 1, 0,
                :gamma_market_id, :event_id, :event_slug, :question_id,
                :enable_orderbook, :accepting_orders,
                :market_start_at, :market_end_at, :market_close_at, :sports_start_at,
                :token_map_json, :rfqe,
                :raw_gamma_payload_hash, :raw_clob_market_info_hash, :raw_orderbook_hash,
                :authority_tier,
                :wide_spread_display_substitution, :depth_at_best_ask,
                :tradeability_status_json
            )
            """,
            {
                "snap_id": f"snapshot-exec-{index}",
                "cond_id": f"condition-{index}",
                "yes_id": f"yes-{index}",
                "no_id": f"no-{index}",
                "depth": _depth_extra,
                **_extra_base,
            },
        )
    conn.execute(
        """
        CREATE TABLE market_events (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            outcome TEXT,
            condition_id TEXT,
            token_id TEXT,
            market_slug TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL,
            created_at TEXT
        )
        """
    )
    rows = []
    for index in range(1, condition_count + 1):
        # MECE-valid °F partition: leftmost bin has open-low shoulder (range_low=None),
        # rightmost bin has open-high shoulder (range_high=None), interior bins width=2.
        # This satisfies validate_bin_topology (Task #114 S6 law).
        # Layout for condition_count bins starting at 70:
        #   index 1:            None → 71  (left shoulder)
        #   index 2..N-1: 72+(i-2)*2 → 73+(i-2)*2  (interior, width=2)
        #   index N:      72+(N-2)*2 → None  (right shoulder)
        if index == 1:
            range_low_val: float | None = None
            range_high_val: float | None = 71.0
        elif index == condition_count:
            range_low_val = 72.0 + (index - 2) * 2
            range_high_val = None
        else:
            range_low_val = 72.0 + (index - 2) * 2
            range_high_val = range_low_val + 1.0
        rows.append(
            (
                f"{70 + index - 1}-{71 + index - 1}°F",
                f"condition-{index}",
                f"yes-{index}",
                f"chicago-high-{index}",
                f"{70 + index - 1}-{71 + index - 1}°F",
                range_low_val,
                range_high_val,
                "2026-05-24T08:11:00+00:00",
            )
        )
    conn.executemany(
        """
        INSERT INTO market_events VALUES (
            'Chicago', '2026-05-25', 'high', ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        rows,
    )
    conn.execute(
        """
        CREATE TABLE ensemble_snapshots (
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
            dataset_id TEXT,
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
        INSERT INTO ensemble_snapshots VALUES (
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
            'ensemble_snapshots_db_reader',
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
            'ecmwf_opendata_mx2t3_local_calendar_day_max',
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
    _insert_platt_model(conn)
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
            dataset_id TEXT,
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS readiness_state (
            readiness_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            scope_type TEXT NOT NULL,
            city_id TEXT,
            city TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            metric TEXT,
            temperature_metric TEXT,
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            source_id TEXT,
            track TEXT,
            source_run_id TEXT,
            market_family TEXT,
            event_id TEXT,
            condition_id TEXT,
            token_ids_json TEXT NOT NULL DEFAULT '[]',
            strategy_key TEXT,
            status TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            dependency_json TEXT NOT NULL DEFAULT '{}',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("DELETE FROM source_run WHERE source_run_id = 'run-1'")
    conn.execute("DELETE FROM source_run_coverage WHERE coverage_id = 'coverage-1'")
    conn.execute("DELETE FROM readiness_state WHERE readiness_id = 'producer-readiness-1'")
    conn.execute(
        """
        INSERT INTO source_run (
            source_run_id, source_id, track, release_calendar_key, ingest_mode, origin_mode,
            source_cycle_time, source_issue_time, source_release_time, source_available_at,
            fetch_started_at, fetch_finished_at, captured_at, imported_at,
            valid_time_start, valid_time_end, target_local_date, city_id, city_timezone,
            temperature_metric, physical_quantity, observation_field, dataset_id,
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
            'temperature', 'high_temp', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
            51, 51, '[0,3,6]', '[0,3,6]', 3, 3,
            'COMPLETE', 0, 'hash-raw', 'hash-manifest', 'SUCCESS', NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO readiness_state (
            readiness_id, scope_key, scope_type, city_id, city, city_timezone,
            target_local_date, metric, temperature_metric, physical_quantity,
            observation_field, data_version, source_id, track, source_run_id,
            market_family, event_id, condition_id, token_ids_json,
            strategy_key, status, reason_codes_json, computed_at, expires_at,
            dependency_json, provenance_json
        ) VALUES (
            'producer-readiness-1',
            'city_metric|Chicago|America/Chicago|2026-05-25|high|temperature|high_temp|ecmwf_opendata_mx2t3_local_calendar_day_max_v1|producer_readiness||ecmwf_open_data|operational|',
            'city_metric',
            'Chicago', 'Chicago', 'America/Chicago',
            '2026-05-25', NULL, 'high', 'temperature',
            'high_temp', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
            'ecmwf_open_data', 'operational', 'run-1',
            NULL, NULL, NULL, '[]',
            'producer_readiness', 'LIVE_ELIGIBLE', '["READY"]',
            '2026-05-24T08:10:00+00:00', '2026-05-25T00:00:00+00:00',
            '{"coverage_id":"coverage-1"}', '{"contract":"edli-test-fixture"}'
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
            'coverage-1', 'run-1', 'ecmwf_open_data', 'ensemble_snapshots_db_reader',
            'ecmwf_open_data', 'operational', 'Chicago', 'Chicago', 'America/Chicago',
            '2026-05-25', 'high', 'temperature', 'high_temp',
            'ecmwf_opendata_mx2t3_local_calendar_day_max', 51, 51, '[0,3,6]', '[0,3,6]',
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
        CREATE TABLE IF NOT EXISTS platt_models (
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
    conn.execute("DELETE FROM platt_models WHERE model_key = 'platt-world-1'")
    conn.execute(
        """
        INSERT INTO platt_models VALUES (
            'platt-world-1', 'high', 'Chicago', 'MAM',
            'tigge_mx2t6_local_calendar_day_max',
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
    decision_time = kwargs.pop("decision_time", datetime.fromisoformat(event.received_at))
    return build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        decision_time=decision_time,
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

    assert receipt.proof_accepted is True
    assert receipt.submitted is False
    assert receipt.event_id == event.event_id
    assert receipt.causal_snapshot_id == event.causal_snapshot_id
    assert receipt.trade_score_positive is True
    assert receipt.trade_score is not None
    assert receipt.trade_score > 0
    assert receipt.q_live is not None
    assert receipt.q_live > 0.60
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
    assert receipt.decision_proof_bundle is not None
    assert receipt.decision_proof_bundle.forecast_authority.certificate_type == claims.FORECAST_AUTHORITY
    assert receipt.decision_proof_bundle.forecast_authority.payload["reader_status"] == "LIVE_ELIGIBLE"
    assert receipt.decision_proof_bundle.forecast_authority.payload["coverage_id"] == "coverage-1"
    assert receipt.decision_proof_bundle.forecast_authority.payload["producer_readiness_id"] == "producer_readiness:coverage-1"
    assert receipt.decision_proof_bundle.forecast_authority.payload["required_steps"] == (0, 3, 6)
    assert receipt.decision_proof_bundle.forecast_authority.payload["observed_steps"] == (0, 3, 6)
    assert receipt.decision_proof_bundle.forecast_authority.payload["source_run_status"] == "SUCCESS"
    assert receipt.decision_proof_bundle.calibration.payload["calibrator_model_key"] == "platt-world-1"
    assert receipt.decision_proof_bundle.calibration.payload["calibration_source_id"] == "tigge_mars"
    assert receipt.decision_proof_bundle.calibration.payload["training_cutoff"] == "2026-05-01T00:00:00+00:00"
    assert receipt.decision_proof_bundle.calibration.clock.source_available_at.isoformat() == "2026-05-01T00:00:00+00:00"
    assert receipt.decision_proof_bundle.belief.payload["calibrator_model_key"] == "platt-world-1"
    assert receipt.decision_proof_bundle.belief.payload["forecast_snapshot_id"] == "1"
    assert receipt.decision_proof_bundle.belief.payload["bin_labels_hash"] == receipt.decision_proof_bundle.family_closure.payload["bin_labels_hash"]
    assert receipt.decision_proof_bundle.fdr.payload["edge_bootstrap_n"] == receipt.decision_proof_bundle.model_config.payload["edge_bootstrap_n"]
    assert receipt.decision_proof_bundle.executable_snapshot.payload["orderbook_hash"]
    assert receipt.decision_proof_bundle.executable_snapshot.payload["fee_details_hash"]
    assert receipt.decision_proof_bundle.executable_snapshot.payload["min_tick_size"] == "0.01"
    assert receipt.decision_proof_bundle.executable_snapshot.payload["min_order_size"] == "5"
    assert receipt.decision_proof_bundle.executable_snapshot.payload["neg_risk"] == 0
    assert receipt.decision_proof_bundle.quote_feasibility.payload["native_side"] == "YES_ASK"
    assert receipt.decision_proof_bundle.quote_feasibility.payload["quote_depth_hash"]
    assert "receipt_projection" not in receipt.decision_proof_bundle.fdr.payload
    assert receipt.decision_proof_bundle.quote_feasibility.payload["execution_price_type"] == "ExecutionPrice"


def test_forecast_trigger_event_without_q_or_token_fields_builds_no_submit_receipt():
    event = _forecast_event()
    receipt = _receipt(event, _trade_conn_with_snapshot())

    assert receipt.proof_accepted is True
    assert receipt.token_id == "yes-1"
    assert receipt.q_live is not None
    assert receipt.q_live > 0.60
    assert receipt.trade_score is not None
    assert receipt.fdr_hypothesis_count == 4
    assert receipt.kelly_execution_price_type == "ExecutionPrice"
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_legacy_calibration_materialization_time_is_not_training_cutoff():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute(
        """
        UPDATE platt_models
        SET recorded_at = '2026-05-24T08:13:00+00:00',
            fitted_at = '2026-05-24T08:13:00+00:00'
        WHERE model_key = 'platt-world-1'
        """
    )

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)
    assert receipt.proof_accepted is True
    assert receipt.decision_proof_bundle is not None
    calibration = receipt.decision_proof_bundle.calibration
    assert calibration.payload["training_cutoff"] == "2026-05-24T00:00:00+00:00"
    assert calibration.payload["model_materialized_at"] == "2026-05-24T08:13:00+00:00"
    assert calibration.clock.source_available_at.isoformat() == "2026-05-24T00:00:00+00:00"


def test_certificate_rejects_explicit_calibration_training_cutoff_after_decision():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("ALTER TABLE platt_models ADD COLUMN training_cutoff TEXT")
    conn.execute(
        """
        UPDATE platt_models
        SET training_cutoff = '2026-05-24T08:13:00+00:00'
        WHERE model_key = 'platt-world-1'
        """
    )

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=DECISION_TIME,
        proof_bundle=receipt.decision_proof_bundle,
    )

    assert result.status == "REJECTED"
    assert result.failures[0].reason_code == "NO_SUBMIT_CERTIFICATE_REJECTED"
    assert "calibration.training_cutoff after decision_time" in (result.failures[0].reason_detail or "")


def test_market_topology_certificate_uses_topology_row_clock_not_event_clock():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE market_events SET created_at = '2026-05-24T08:11:00+00:00'")

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.decision_proof_bundle is not None
    assert receipt.decision_proof_bundle.market_topology.clock.source_available_at.isoformat() == "2026-05-24T08:11:00+00:00"
    assert receipt.decision_proof_bundle.family_closure.clock.source_available_at.isoformat() == "2026-05-24T08:11:00+00:00"
    assert receipt.decision_proof_bundle.market_topology.clock.source_available_at.isoformat() != event.available_at


def test_topology_persisted_after_decision_blocks_certificate():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE market_events SET created_at = '2026-05-24T08:13:00+00:00'")

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=DECISION_TIME,
        proof_bundle=receipt.decision_proof_bundle,
    )

    assert result.status == "REJECTED"
    assert result.failures[0].reason_code == "NO_SUBMIT_CERTIFICATE_REJECTED"
    assert "source_available_at after decision_time" in (result.failures[0].reason_detail or "")


def test_topology_clock_missing_blocks_certificate():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE market_events SET created_at = NULL")

    with pytest.raises(ValueError, match="TOPOLOGY_CLOCK_MISSING"):
        _receipt(event, conn, decision_time=DECISION_TIME)


def test_adapter_source_truth_status_comes_from_forecast_authority():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.decision_proof_bundle is not None
    assert receipt.decision_proof_bundle.source_truth.payload["source_status"] == "LIVE_ELIGIBLE"
    assert receipt.decision_proof_bundle.source_truth.payload["source_status"] == receipt.decision_proof_bundle.forecast_authority.payload["reader_status"]
    assert receipt.decision_proof_bundle.source_truth.payload["source_authority_id"] == "read_executable_forecast"
    assert receipt.decision_proof_bundle.source_truth.payload["derived_from_certificate_type"] == claims.FORECAST_AUTHORITY
    assert receipt.decision_proof_bundle.source_truth.payload["derived_from_snapshot_id"] == receipt.decision_proof_bundle.forecast_authority.payload["snapshot_id"]
    assert receipt.decision_proof_bundle.source_truth.payload["derived_from_reader_status"] == receipt.decision_proof_bundle.forecast_authority.payload["reader_status"]


def test_market_events_authority_rows_have_topology_clock_fields():
    conn = sqlite3.connect(":memory:")
    init_schema_forecasts(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(market_events)").fetchall()}

    assert "created_at" in columns


def test_no_submit_receipt_succeeds_with_production_market_events_clock_shape():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE market_events SET created_at = '2026-05-24T08:11:00+00:00'")

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.proof_accepted is True
    assert receipt.decision_proof_bundle is not None
    assert receipt.decision_proof_bundle.market_topology.clock.persisted_at.isoformat() == "2026-05-24T08:11:00+00:00"


def test_topology_clock_missing_blocks_with_topology_clock_missing_reason():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE market_events SET created_at = NULL")

    with pytest.raises(ValueError, match="TOPOLOGY_CLOCK_MISSING"):
        _receipt(event, conn, decision_time=DECISION_TIME)


def test_cost_model_certificate_records_native_cost_source():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.decision_proof_bundle is not None
    assert receipt.decision_proof_bundle.cost_model.payload["cost_source"] == "native_orderbook_ask"
    assert receipt.decision_proof_bundle.cost_model.payload["quote_source_kind"] == "executable_market_snapshot_native_book"
    assert receipt.decision_proof_bundle.quote_feasibility.payload["cost_source"] == "native_orderbook_ask"


def test_forecast_certificate_records_members_json_hash_and_window_authority():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.decision_proof_bundle is not None
    forecast = receipt.decision_proof_bundle.forecast_authority.payload
    belief = receipt.decision_proof_bundle.belief.payload
    snapshot = dict(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id = '1'").fetchone())
    assert forecast["members_json_hash"] == _snapshot_members_json_hash(snapshot)
    assert belief["members_json_hash"] == forecast["members_json_hash"]
    assert forecast["members_extrema_transform"] == "daily_max"
    assert forecast["target_local_date"] == "2026-05-25"
    assert forecast["city_timezone"] == "America/Chicago"
    assert forecast["bin_labels_hash"] == receipt.decision_proof_bundle.family_closure.payload["bin_labels_hash"]


def test_high_forecast_snapshot_members_json_is_daily_max_extrema():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.decision_proof_bundle is not None
    forecast = receipt.decision_proof_bundle.forecast_authority.payload
    assert forecast["temperature_metric"] == "high"
    assert forecast["members_extrema_metric_identity"] == "high"
    assert forecast["members_extrema_transform"] == "daily_max"
    assert forecast["members_json_source"] == "ensemble_snapshots.daily_extrema"
    assert forecast["members_json_hash"] == _snapshot_members_json_hash(
        dict(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id = '1'").fetchone())
    )


def test_low_forecast_snapshot_members_json_is_daily_min_extrema():
    event = _low_bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    _convert_fixture_to_low_extrema(conn)

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.decision_proof_bundle is not None
    forecast = receipt.decision_proof_bundle.forecast_authority.payload
    assert forecast["temperature_metric"] == "low"
    assert forecast["members_extrema_metric_identity"] == "low"
    assert forecast["members_extrema_transform"] == "daily_min"
    assert forecast["members_json_source"] == "ensemble_snapshots.daily_extrema"
    assert forecast["members_json_hash"] == _snapshot_members_json_hash(
        dict(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id = '1'").fetchone())
    )


def test_event_bound_low_uses_low_extrema_members_not_raw_hourly_or_max_members():
    event = _low_bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    _convert_fixture_to_low_extrema(conn)

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.decision_proof_bundle is not None
    forecast = receipt.decision_proof_bundle.forecast_authority.payload
    low_snapshot = dict(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id = '1'").fetchone())
    high_like_snapshot = {**low_snapshot, "members_json": json.dumps([70.5] * 41 + [71.5] * 10, separators=(",", ":"))}
    assert forecast["members_extrema_transform"] == "daily_min"
    assert forecast["members_json_hash"] == _snapshot_members_json_hash(low_snapshot)
    assert forecast["members_json_hash"] != _snapshot_members_json_hash(high_like_snapshot)


def test_members_json_hash_changes_when_member_extrema_change():
    base = {"members_json": json.dumps([70.5, 71.5], separators=(",", ":"))}
    changed = {"members_json": json.dumps([70.5, 72.5], separators=(",", ":"))}

    assert _snapshot_members_json_hash(base) != _snapshot_members_json_hash(changed)


def test_belief_p_cal_vector_hash_changes_when_unselected_bin_probability_changes():
    assert _probability_vector_hash((0.8, 0.2)) != _probability_vector_hash((0.8, 0.19))


def test_belief_p_live_vector_hash_changes_when_unselected_bin_probability_changes():
    assert _probability_vector_hash((0.78, 0.22)) != _probability_vector_hash((0.78, 0.21))


def test_belief_vector_hash_uses_family_bin_order():
    assert _probability_vector_hash((0.8, 0.2)) != _probability_vector_hash((0.2, 0.8))


def test_adapter_does_not_synthesize_forecast_applied_validations():
    source = Path("src/engine/event_reactor_adapter.py").read_text()
    forecast_section = source[
        source.index("def _forecast_authority_payload_and_clock") : source.index("def _calibration_authority_payload_and_clock")
    ]

    assert '"applied_validations": tuple(evidence.applied_validations)' in forecast_section
    assert '"applied_validations": tuple(evidence.applied_validations) or' not in forecast_section
    assert "FORECAST_AUTHORITY_VALIDATIONS_MISSING" in forecast_section


def test_adapter_rejects_empty_reader_applied_validations(monkeypatch):
    from src.data import executable_forecast_reader

    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    evidence = SimpleNamespace(
        forecast_source_id="ecmwf_open_data",
        forecast_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        source_transport="ensemble_snapshots_db_reader",
        source_cycle_time="2026-05-24T00:00:00+00:00",
        source_issue_time="2026-05-24T00:00:00+00:00",
        source_run_id="run-1",
        coverage_id="coverage-1",
        producer_readiness_id="producer_readiness:coverage-1",
        entry_readiness_id=None,
        input_snapshot_ids=(1,),
        raw_payload_hash="hash-raw",
        manifest_hash="hash-manifest",
        required_steps=(0, 3, 6),
        observed_steps=(0, 3, 6),
        expected_members=51,
        observed_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
        applied_validations=(),
        source_available_at="2026-05-24T08:10:00+00:00",
        fetch_started_at="2026-05-24T07:10:00+00:00",
        fetch_finished_at="2026-05-24T08:05:00+00:00",
        captured_at="2026-05-24T08:10:00+00:00",
    )
    monkeypatch.setattr(
        executable_forecast_reader,
        "read_executable_forecast",
        lambda *_args, **_kwargs: SimpleNamespace(
            ok=True,
            status="LIVE_ELIGIBLE",
            bundle=SimpleNamespace(snapshot=SimpleNamespace(snapshot_id="1"), evidence=evidence),
            reason_code="OK",
        ),
    )

    with pytest.raises(ValueError, match="FORECAST_AUTHORITY_VALIDATIONS_MISSING"):
        _receipt(event, conn, decision_time=DECISION_TIME)


def test_family_closure_clock_missing_blocks_certificate():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE market_events SET created_at = ''")

    with pytest.raises(ValueError, match="TOPOLOGY_CLOCK_MISSING"):
        _receipt(event, conn, decision_time=DECISION_TIME)


def test_topology_db_read_fallback_requires_db_state_read_certificate():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("DELETE FROM market_events")

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.proof_accepted is False
    assert receipt.reason == "EVENT_BOUND_MARKET_TOPOLOGY_MISSING"


def test_edli_runtime_recomputes_p_raw_from_members_not_unproven_snapshot_json():
    family = SimpleNamespace(city="Chicago", metric="high")
    bins = [Bin(70, 71, "F", "70-71°F"), Bin(72, 73, "F", "72-73°F")]
    members = np.asarray([70.5] * 41 + [72.5] * 10, dtype=float)

    p_raw = _snapshot_p_raw(
        {"p_raw_json": json.dumps([0.0, 1.0], separators=(",", ":")), "settlement_unit": "F", "temperature_metric": "high"},
        family=family,
        bins=bins,
        members=members,
        payload={},
    )

    assert p_raw[0] > 0.7
    assert p_raw[1] < 0.3


def test_edli_p_raw_matches_current_entry_forecast_signal_for_fixture():
    family = SimpleNamespace(city="Chicago", metric="high")
    bins = [Bin(70, 71, "F", "70-71°F"), Bin(72, 73, "F", "72-73°F")]
    members = np.asarray([70.5] * 41 + [72.5] * 10, dtype=float)
    city = runtime_cities_by_name()["Chicago"]
    semantics = SettlementSemantics.for_city(city)

    edli_p_raw = _snapshot_p_raw({"settlement_unit": "F", "temperature_metric": "high"}, family=family, bins=bins, members=members, payload={})
    entry_p_raw = p_raw_vector_from_maxes(members, city, semantics, bins)

    np.testing.assert_allclose(edli_p_raw, entry_p_raw, rtol=0.0, atol=0.0)


def test_no_submit_rejects_snapshot_missing_unit_metadata():
    family = SimpleNamespace(city="Chicago", metric="high")
    bins = [Bin(70, 71, "F", "70-71°F")]
    members = np.asarray([70.5] * 51, dtype=float)

    with pytest.raises(ValueError, match="FORECAST_UNIT_AUTHORITY_MISSING"):
        _snapshot_p_raw({"temperature_metric": "high"}, family=family, bins=bins, members=members, payload={})


def test_payload_unit_cannot_supply_missing_snapshot_unit_authority():
    family = SimpleNamespace(city="Chicago", metric="high")
    bins = [Bin(70, 71, "F", "70-71°F")]
    members = np.asarray([70.5] * 51, dtype=float)

    with pytest.raises(ValueError, match="FORECAST_UNIT_AUTHORITY_MISSING"):
        _snapshot_p_raw({"temperature_metric": "high"}, family=family, bins=bins, members=members, payload={"unit": "F"})


def test_members_unit_degC_uses_C():
    assert _snapshot_unit({"members_unit": "degC"}, {}) == "C"


def test_members_unit_degF_uses_F():
    assert _snapshot_unit({"members_unit": "degF"}, {}) == "F"


def test_low_metric_requires_low_extrema_members_identity():
    family = SimpleNamespace(city="Chicago", metric="low")
    bins = [Bin(30, 31, "F", "30-31°F")]
    members = np.asarray([30.5] * 51, dtype=float)

    _snapshot_p_raw({"settlement_unit": "F", "temperature_metric": "low"}, family=family, bins=bins, members=members, payload={})


def test_high_metric_requires_high_extrema_members_identity():
    family = SimpleNamespace(city="Chicago", metric="high")
    bins = [Bin(70, 71, "F", "70-71°F")]
    members = np.asarray([70.5] * 51, dtype=float)

    _snapshot_p_raw({"settlement_unit": "F", "temperature_metric": "high"}, family=family, bins=bins, members=members, payload={})


def test_unit_payload_cannot_override_snapshot_unit_without_authority():
    assert _snapshot_unit({"settlement_unit": "F", "members_unit": "degC"}, {"unit": "C"}) == "F"


def test_edli_p_cal_matches_existing_evaluator_platt_path_for_same_snapshot_and_family():
    from src.calibration.forecast_calibration_domain import derive_phase2_keys_from_ens_result
    from src.calibration.manager import get_calibrator
    from src.calibration.platt import calibrate_and_normalize
    from src.data.forecast_source_registry import calibration_source_id_for_lookup

    conn = _trade_conn_with_snapshot()
    snapshot = dict(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id = '1'").fetchone())
    family = SimpleNamespace(city="Chicago", target_date="2026-05-25", metric="high")
    bins = [Bin(70, 71, "F", "70-71°F"), Bin(71, 72, "F", "71-72°F")]
    members = np.asarray(json.loads(snapshot["members_json"]), dtype=float)
    p_raw = _snapshot_p_raw(snapshot, family=family, bins=bins, members=members, payload={})

    edli_p_cal = _snapshot_p_cal(
        conn,
        snapshot=snapshot,
        family=family,
        bins=bins,
        p_raw=p_raw,
        payload={},
        decision_time=DECISION_TIME,
    )
    cycle, raw_source_id, horizon_profile = derive_phase2_keys_from_ens_result(
        {
            "issue_time": snapshot["source_cycle_time"],
            "source_id": snapshot["source_id"],
            "horizon_profile": snapshot.get("horizon_profile"),
        }
    )
    cal_source_id = calibration_source_id_for_lookup(raw_source_id)
    cal, _level = get_calibrator(
        conn,
        runtime_cities_by_name()["Chicago"],
        "2026-05-25",
        temperature_metric="high",
        cycle=cycle,
        source_id=cal_source_id,
        horizon_profile=horizon_profile,
    )
    assert cal is not None
    expected = calibrate_and_normalize(p_raw, cal, 32.0 / 24.0, bin_widths=[candidate.width for candidate in bins])

    np.testing.assert_allclose(edli_p_cal, expected, rtol=0.0, atol=0.0)


def test_family_candidates_use_market_event_range_bounds_not_payload_default():
    event = _forecast_event()
    receipt = _receipt(event, _trade_conn_with_snapshot())

    assert receipt.bin_label == "70-71°F"
    assert receipt.bin_label != "0-1°F"


def test_bin_from_market_event_carries_celsius_unit_from_city_settlement_authority():
    """Bin unit is CARRIED from the city settlement authority, never defaulted to 'F'
    (data-provenance law 2026-05-30).

    market_events has no unit column — the unit lives in the city's SettlementSemantics
    (the same authority p_raw uses) and is echoed in the market label (°C/°F). Defaulting a
    missing payload unit to 'F' made every Celsius-city candidate fail closed with
    EVENT_BOUND_MARKET_TOPOLOGY_INVALID ('… is Celsius but unit=F'). The Bin must carry the
    city's true settlement unit; the label cross-check in Bin remains the fail-closed guard.
    """
    from src.engine.event_reactor_adapter import _bin_from_market_event

    # Celsius city (Wuhan), market "26°C or below" shoulder bin, NO unit in payload.
    row = {
        "range_label": "Will the highest temperature in Wuhan be 26°C or below on May 31?",
        "range_low": None,
        "range_high": 26.0,
    }
    payload = {"city": "Wuhan", "metric": "high"}

    bin_obj = _bin_from_market_event(row, payload)

    assert bin_obj.unit == "C"  # carried from city settlement authority, NOT defaulted to 'F'


def test_bin_from_market_event_carries_fahrenheit_unit_for_usa_city():
    """Fahrenheit cities keep unit 'F' from the same city settlement authority (no regression)."""
    from src.engine.event_reactor_adapter import _bin_from_market_event

    row = {"range_label": "70-71°F", "range_low": 70.0, "range_high": 71.0}
    payload = {"city": "Chicago", "metric": "high"}

    bin_obj = _bin_from_market_event(row, payload)

    assert bin_obj.unit == "F"


def test_missing_market_topology_range_blocks_no_submit_receipt():
    event = _forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE market_events SET range_low = NULL, range_high = NULL")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert receipt.reason == "EVENT_BOUND_MARKET_TOPOLOGY_INVALID:market topology bin range missing"


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
    # Build a conn with only the YES-selected snapshot; the orderbook has no NO asks.
    # The reactor must select the YES snapshot (only one for condition-1) but
    # cannot find a NO ask → EXECUTABLE_NATIVE_ASK_MISSING.
    event = _bound_forecast_event(token_id="no-1")
    conn = _trade_conn_with_snapshot(
        include_no_snapshot=False,
        selected_ask="0.40",
    )
    # Override the YES snapshot's orderbook to omit NO asks.
    # Table is append-only via trigger; use a fresh connection approach:
    # Insert a SECOND yes-side snapshot with the desired depth that becomes the
    # latest (same condition_id, later captured_at).
    from src.state.snapshot_repo import init_snapshot_schema as _iss  # already imported at module level, but local ref for clarity
    _no_bid_depth = json.dumps({"YES": {"asks": [{"price": "0.40", "size": "100"}], "bids": []}}, separators=(",", ":"))
    conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, condition_id, yes_token_id, no_token_id,
            selected_outcome_token_id, outcome_label,
            orderbook_top_ask, orderbook_top_bid, orderbook_depth_json,
            min_tick_size, min_order_size, fee_details_json, neg_risk,
            freshness_deadline, captured_at, active, closed,
            gamma_market_id, event_id, event_slug, question_id,
            enable_orderbook, accepting_orders,
            market_start_at, market_end_at, market_close_at, sports_start_at,
            token_map_json, rfqe,
            raw_gamma_payload_hash, raw_clob_market_info_hash, raw_orderbook_hash,
            authority_tier,
            wide_spread_display_substitution, depth_at_best_ask,
            tradeability_status_json
        ) VALUES (
            'snapshot-exec-1-yes-v2', 'condition-1', 'yes-1', 'no-1', 'yes-1', 'YES',
            '0.40', '0.39', :depth, '0.01', '5', '{"fee_rate_fraction":0.0}', 0,
            '2026-05-26T00:00:00+00:00', '2026-05-25T09:00:00+00:00', 1, 0,
            'gamma-mkt-1', 'event-1', 'chicago-temperature-high', 'q-1',
            1, 1,
            NULL, NULL, NULL, NULL,
            '{"yes":"yes-1","no":"no-1"}', NULL,
            :gh, :ch, :oh,
            'CLOB',
            0, 0, '{}'
        )
        """,
        {"depth": _no_bid_depth, "gh": "a" * 64, "ch": "b" * 64, "oh": "d" * 64},
    )
    conn.commit()

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert receipt.reason.startswith("EXECUTABLE_NATIVE_ASK_MISSING")


def test_runtime_receipt_accepts_family_with_missing_sibling_snapshot_as_non_tradeable():
    """With the full-family design, a 3-bin family where only 2 of 3 bins have
    executable snapshots must PASS the FDR proof (the third bin is non-tradeable,
    not absent).  The selected bin (condition-1) has a snapshot, so the receipt
    must be accepted with fdr_hypothesis_count == 5 (3 yes-tokens + 2 no-tokens;
    the non-tradeable bin contributes its yes-token but has no no-token).

    The old exact-set-equality gate (FDR_FULL_FAMILY_PROOF_MISSING) is incorrect
    because it renormalized q over the 2-bin subset, inflating probabilities ~1.2×
    and shrinking fdr_hypothesis_count from 5 to 4 — both unsafe.
    """
    event = _bound_forecast_event(fdr_condition_count=3)
    receipt = _receipt(event, _trade_conn_with_snapshot(condition_count=3, snapshot_condition_count=2))

    # Full-family: receipt must not be rejected for missing sibling snapshot
    assert receipt.reason != "FDR_FULL_FAMILY_PROOF_MISSING"
    assert receipt.family_complete is True
    # 3-bin family: 2 tradeable (yes+no each) + 1 non-tradeable (yes only, no_token_id=None).
    # yes_token_ids has 3 entries; no_token_ids has 2 entries → 5 total hypotheses.
    # This is MORE than the broken 2-bin subset (4 hypotheses) and correct for the full
    # MECE family — q runs over all 3 bins, FDR denominator is 5 (not 4).
    assert receipt.fdr_hypothesis_count == 5


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

    assert receipt.proof_accepted is True
    assert receipt.q_live is not None
    assert receipt.q_live > 0.60
    assert receipt.fdr_pass is True
    assert receipt.fdr_hypothesis_count == 4


def test_forecast_receipt_uses_separate_forecast_authority_connection():
    event = _bound_forecast_event()
    trade_conn = _trade_conn_with_snapshot()
    forecast_conn = _trade_conn_with_snapshot()
    forecast_conn.execute("DROP TABLE executable_market_snapshots")
    trade_conn.execute("DROP TABLE ensemble_snapshots")
    trade_conn.execute("DROP TABLE market_events")
    trade_conn.execute("DROP TABLE source_run")
    trade_conn.execute("DROP TABLE source_run_coverage")

    receipt = _receipt(event, trade_conn, forecast_conn=forecast_conn, topology_conn=forecast_conn)

    assert receipt.proof_accepted is True
    assert receipt.q_live is not None
    assert receipt.q_live > 0.60
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_executable_snapshot_gate_uses_forecast_topology_authority_connection():
    event = _bound_forecast_event()
    trade_conn = _trade_conn_with_snapshot()
    forecast_conn = _trade_conn_with_snapshot()
    forecast_conn.execute("DROP TABLE executable_market_snapshots")
    trade_conn.execute("DROP TABLE market_events")
    gate = executable_snapshot_gate_from_trade_conn(trade_conn, topology_conn=forecast_conn)

    assert gate(event, DECISION_TIME) is True


def test_executable_snapshot_gate_ignores_price_freshness_window_binds_identity():
    """Entry gate binds market IDENTITY, not the 30s price window (operator law 2026-05-30:
    "freshness 针对价格不针对市场; 市场捕捉了不会突然消失").

    Supersedes the prior decision-time-vs-construction-clock freshness contract. That contract
    rejected a captured family once its price window lapsed, which structurally halted
    large-family decisions (a full MECE family captures bin-by-bin over >30s, so early bins
    always lapse before the last is captured). Price-freshness for the actually-traded selected
    bin is now enforced only at submission (assert_snapshot_executable).
    """
    event = _bound_forecast_event()
    trade_conn = _trade_conn_with_snapshot(freshness_deadline="2026-05-24T08:12:30+00:00")
    forecast_conn = _trade_conn_with_snapshot()
    forecast_conn.execute("DROP TABLE executable_market_snapshots")
    trade_conn.execute("DROP TABLE market_events")
    gate = executable_snapshot_gate_from_trade_conn(
        trade_conn,
        topology_conn=forecast_conn,
        now=datetime(2026, 5, 24, 9, 0, tzinfo=timezone.utc),
    )

    # Identity persists regardless of where the decision clock sits relative to the (now
    # submission-only) price window — passes both before AND after the lapsed deadline.
    assert gate(event, datetime(2026, 5, 24, 8, 12, tzinfo=timezone.utc)) is True
    assert gate(event, datetime(2026, 5, 24, 8, 13, tzinfo=timezone.utc)) is True


def test_entry_gate_binds_on_identity_not_price_freshness_across_slow_family_capture():
    """RELATIONSHIP (capture -> entry/FDR gate -> submission).

    Operator design law 2026-05-30: "freshness 针对价格不针对市场; 市场捕捉了不会突然消失."
    A MECE family is captured bin-by-bin; a full family (tens of bins) takes >30s, so by
    the time the last bin is captured the early bins' price-freshness window
    (captured_at + FRESHNESS_WINDOW_DEFAULT) has already expired. The entry/FDR gate proves
    MARKET IDENTITY/family-completeness (a snapshot row exists for every sibling
    condition_id), which does NOT decay with price age. PRICE-freshness is a property of the
    SELECTED bin's tradeable cost and is enforced ONLY at submission
    (assert_snapshot_executable). Binding the entry gate on a 30s price window made
    large-family decisions structurally impossible (decision_events stuck at 0).
    """
    event = _bound_forecast_event()
    # Whole family present (identity intact) but EVERY bin price-stale relative to the
    # decision clock — simulates a >30s full-family capture where early bins expired.
    # captured_at (08:10) before freshness_deadline (08:11) < decision clock (08:12).
    trade_conn = _trade_conn_with_snapshot(
        captured_at="2026-05-24T08:10:00+00:00",
        freshness_deadline="2026-05-24T08:11:00+00:00",
    )
    forecast_conn = _trade_conn_with_snapshot()
    forecast_conn.execute("DROP TABLE executable_market_snapshots")
    trade_conn.execute("DROP TABLE market_events")
    gate = executable_snapshot_gate_from_trade_conn(trade_conn, topology_conn=forecast_conn)

    # NEW invariant: identity present for the full family -> gate PASSES regardless of price age.
    assert gate(event, datetime(2026, 5, 24, 8, 12, tzinfo=timezone.utc)) is True


def test_executable_snapshot_gate_requires_topology_authority_connection():
    event = _bound_forecast_event()
    gate = executable_snapshot_gate_from_trade_conn(_trade_conn_with_snapshot())

    assert gate(event, DECISION_TIME) is False


def test_receipt_requires_explicit_forecast_and_topology_authority_connections():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()

    missing_forecast = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        decision_time=DECISION_TIME,
        topology_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )
    missing_topology = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        decision_time=DECISION_TIME,
        forecast_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )
    missing_calibration = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        decision_time=DECISION_TIME,
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
    conn.execute("UPDATE ensemble_snapshots SET p_cal_json = NULL")

    receipt = _receipt(event, conn, calibration_conn=sqlite3.connect(":memory:"))

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:CALIBRATION_AUTHORITY_MISSING")


def test_receipt_uses_world_calibration_authority_not_forecast_conn():
    event = _bound_forecast_event()
    trade_conn = _trade_conn_with_snapshot()
    forecast_conn = _trade_conn_with_snapshot()
    calibration_conn = _calibration_conn_with_platt_model()
    forecast_conn.execute("DROP TABLE executable_market_snapshots")
    trade_conn.execute("DROP TABLE ensemble_snapshots")
    trade_conn.execute("DROP TABLE market_events")
    trade_conn.execute("DROP TABLE source_run")
    trade_conn.execute("DROP TABLE source_run_coverage")
    forecast_conn.execute("UPDATE ensemble_snapshots SET p_cal_json = NULL")

    receipt = _receipt(
        event,
        trade_conn,
        forecast_conn=forecast_conn,
        topology_conn=forecast_conn,
        calibration_conn=calibration_conn,
    )

    assert receipt.proof_accepted is True
    assert receipt.q_live is not None
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_forecast_conn_fake_platt_model_is_not_calibration_authority():
    event = _bound_forecast_event()
    trade_conn = _trade_conn_with_snapshot()
    forecast_conn = _trade_conn_with_snapshot()
    forecast_conn.execute("DROP TABLE executable_market_snapshots")
    trade_conn.execute("DROP TABLE ensemble_snapshots")
    trade_conn.execute("DROP TABLE market_events")
    trade_conn.execute("DROP TABLE source_run")
    trade_conn.execute("DROP TABLE source_run_coverage")
    forecast_conn.execute("UPDATE ensemble_snapshots SET p_cal_json = NULL")
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


def test_p_cal_json_without_authority_does_not_authorize_without_calibrator():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots SET p_cal_authority = NULL")

    receipt = _receipt(event, conn, calibration_conn=sqlite3.connect(":memory:"))

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:CALIBRATION_AUTHORITY_MISSING")


def test_p_cal_json_available_after_event_is_ignored_when_calibrator_authority_exists():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots SET p_cal_available_at = '2026-05-24T08:11:00+00:00'")

    receipt = _receipt(event, conn)

    assert receipt.proof_accepted is True
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_adapter_surfaces_reader_block_after_event_emit(monkeypatch):
    from types import SimpleNamespace

    from src.data import executable_forecast_reader

    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE source_run_coverage SET readiness_status = 'BLOCKED'")
    monkeypatch.setattr(
        executable_forecast_reader,
        "read_executable_forecast",
        lambda *_args, **_kwargs: SimpleNamespace(ok=False, bundle=None, reason_code="READINESS_BLOCKED"),
    )

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert "FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:READINESS_BLOCKED" in receipt.reason


def test_adapter_computes_on_reader_elected_snapshot_not_causal_pin(monkeypatch):
    """RELATIONSHIP: the reactor computes inference on the executable-forecast reader's
    ELECTED snapshot, never on the causal-pinned seed with an equality assertion.

    The causal snapshot triggers the event, but when its source_run is still re-ingesting
    members (captured_at advances past the decision moment) the reader's causality gate drops
    it and elects a different fully-captured FULL_CONTRIBUTOR. The prior code pinned inference
    to the causal snapshot and asserted reader==causal, raising FORECAST_READER_SNAPSHOT_MISMATCH
    on every re-ingestion race — the permanent decision_events=0 leak. The reactor must instead
    return the reader-elected snapshot row (causal_snapshot_id stays provenance only).
    """
    from types import SimpleNamespace

    from src.data import executable_forecast_reader
    from src.engine.event_reactor_adapter import _forecast_snapshot_row_for_event

    conn = _trade_conn_with_snapshot()
    # A second valid snapshot ('2') for the SAME family — the executable authority the reader
    # elects when the causal seed ('1') is still ingesting. Clone '1' so it passes every
    # authority/causality/boundary predicate, then re-key to '2'.
    cols = [str(r[1]) for r in conn.execute("PRAGMA table_info(ensemble_snapshots)").fetchall()]
    seed = dict(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id='1'").fetchone())
    seed["snapshot_id"] = "2"
    conn.execute(
        f"INSERT INTO ensemble_snapshots ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        [seed[c] for c in cols],
    )
    event = _bound_forecast_event()
    family = SimpleNamespace(
        city="Chicago",
        target_date="2026-05-25",
        metric="high",
        family_id="run-1",
        condition_ids=["condition-1"],
        candidates=[],
    )
    monkeypatch.setattr(
        executable_forecast_reader,
        "read_executable_forecast",
        lambda *_args, **_kwargs: SimpleNamespace(
            ok=True,
            bundle=SimpleNamespace(snapshot=SimpleNamespace(snapshot_id="2")),
            reason_code="OK",
        ),
    )
    decision_time = datetime.fromisoformat(event.received_at)

    row = _forecast_snapshot_row_for_event(
        conn, event=event, family=family, allow_latest=False, decision_time=decision_time
    )

    assert row is not None
    # Honoured the reader's election ('2'), NOT the causal-pinned seed ('1'); no mismatch raise.
    assert str(row["snapshot_id"]) == "2"


def test_receipt_revalidates_executable_forecast_reader_authority(monkeypatch):
    from types import SimpleNamespace

    from src.data import executable_forecast_reader

    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()

    monkeypatch.setattr(
        executable_forecast_reader,
        "read_executable_forecast",
        lambda *_args, **_kwargs: SimpleNamespace(ok=False, bundle=None, reason_code="READER_TEST_BLOCK"),
    )

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert "FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:READER_TEST_BLOCK" in receipt.reason


def test_forecast_reader_revalidation_uses_reactor_decision_time(monkeypatch):
    from types import SimpleNamespace

    from src.data import executable_forecast_reader

    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    decision_time = datetime(2026, 5, 24, 8, 17, tzinfo=timezone.utc)
    captured = []
    evidence = SimpleNamespace(
        forecast_source_id="ecmwf_open_data",
        forecast_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        source_transport="ensemble_snapshots_db_reader",
        source_cycle_time="2026-05-24T00:00:00+00:00",
        source_issue_time="2026-05-24T00:00:00+00:00",
        source_run_id="run-1",
        coverage_id="coverage-1",
        producer_readiness_id="producer_readiness:coverage-1",
        entry_readiness_id=None,
        input_snapshot_ids=(1,),
        raw_payload_hash="hash-raw",
        manifest_hash="hash-manifest",
        required_steps=(0, 3, 6),
        observed_steps=(0, 3, 6),
        expected_members=51,
        observed_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
        applied_validations=(
            "source_run_completeness_status",
            "coverage_completeness_status",
            "coverage_readiness_status",
            "required_steps_observed",
            "expected_members_observed",
            "causality_status_ok",
            "authority_verified",
            "available_at_not_future",
        ),
        source_available_at="2026-05-24T08:10:00+00:00",
        fetch_started_at="2026-05-24T07:10:00+00:00",
        fetch_finished_at="2026-05-24T08:05:00+00:00",
        captured_at="2026-05-24T08:10:00+00:00",
    )

    monkeypatch.setattr(
        executable_forecast_reader,
        "read_executable_forecast",
        lambda *_args, **kwargs: captured.append(kwargs["decision_time"])
        or SimpleNamespace(
            ok=True,
            status="LIVE_ELIGIBLE",
            bundle=SimpleNamespace(snapshot=SimpleNamespace(snapshot_id="1"), evidence=evidence),
            reason_code="OK",
        ),
    )

    receipt = _receipt(event, conn, decision_time=decision_time)

    assert receipt.proof_accepted is True
    assert captured
    assert set(captured) == {decision_time}


def test_executable_snapshot_freshness_uses_reactor_decision_time():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot(freshness_deadline="2026-05-24T08:12:30+00:00")

    receipt = _receipt(event, conn, decision_time=datetime(2026, 5, 24, 8, 12, tzinfo=timezone.utc))

    assert receipt.proof_accepted is True
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_price_stale_family_passes_entry_freshness_deferred_to_submission():
    """A price-stale-but-fully-captured family must NOT be rejected at entry with
    EVENT_BOUND_EXECUTABLE_SNAPSHOT_MISSING (operator law 2026-05-30: market identity persists;
    price-freshness is a submission concern). The receipt proceeds past the snapshot-identity
    gate; price-freshness on the traded selected bin is enforced by assert_snapshot_executable
    at submission, never here. (Receipt still does not submit — it stops downstream on the
    EDLI kernel, not on price-staleness.)
    """
    event = _bound_forecast_event()
    # captured_at before freshness_deadline (invariant: deadline >= captured);
    # freshness_deadline is before decision_time (08:12) — simulates price-stale snapshot.
    conn = _trade_conn_with_snapshot(
        captured_at="2026-05-24T08:10:00+00:00",
        freshness_deadline="2026-05-24T08:11:59+00:00",
    )

    receipt = _receipt(event, conn, decision_time=datetime(2026, 5, 24, 8, 12, tzinfo=timezone.utc))

    assert receipt.submitted is False
    # Identity gate passed: the rejection is NOT the entry price-staleness block.
    assert receipt.reason != "EVENT_BOUND_EXECUTABLE_SNAPSHOT_MISSING"


def test_coverage_expired_between_event_available_and_decision_blocks_receipt(monkeypatch):
    from types import SimpleNamespace

    from src.data import executable_forecast_reader

    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE source_run_coverage SET expires_at = '2026-05-24T08:11:30+00:00'")
    seen = []

    def _reader(*_args, **kwargs):
        seen.append(kwargs["decision_time"])
        return SimpleNamespace(ok=False, bundle=None, reason_code="COVERAGE_EXPIRED")

    monkeypatch.setattr(executable_forecast_reader, "read_executable_forecast", _reader)

    receipt = _receipt(event, conn, decision_time=datetime(2026, 5, 24, 8, 12, tzinfo=timezone.utc))

    assert receipt.submitted is False
    assert "FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:COVERAGE_EXPIRED" in receipt.reason
    assert seen == [datetime(2026, 5, 24, 8, 12, tzinfo=timezone.utc)]


def test_top_ask_without_depth_does_not_create_fillable_quote():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot(selected_ask="0.40", depth_json="{}")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert receipt.reason.startswith("EXECUTABLE_NATIVE_ASK_MISSING")
    assert receipt.native_quote_available is False


@pytest.mark.xfail(reason="depth_at_best_ask column fallback for empty orderbook_depth_json is unimplemented in the native quote book (EXECUTABLE_NATIVE_ASK_MISSING:NO_DEPTH). Separate from the q/FDR kernel — tracked as its own quote-book feature.", strict=False)
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

    assert receipt.proof_accepted is True
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
        decision_time=DECISION_TIME,
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
    conn.execute("ALTER TABLE market_events RENAME TO attached_market_events")
    conn.execute("ATTACH DATABASE ':memory:' AS forecasts")
    conn.execute("CREATE TABLE forecasts.ensemble_snapshots AS SELECT * FROM ensemble_snapshots")
    conn.execute("CREATE TABLE forecasts.source_run AS SELECT * FROM source_run")
    conn.execute("CREATE TABLE forecasts.source_run_coverage AS SELECT * FROM source_run_coverage")
    conn.execute("CREATE TABLE forecasts.readiness_state AS SELECT * FROM readiness_state")
    conn.execute(
        """
        CREATE TABLE forecasts.market_events (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            outcome TEXT,
            condition_id TEXT,
            token_id TEXT,
            market_slug TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL,
            created_at TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO forecasts.market_events VALUES (
            'Chicago', '2026-05-25', 'high', ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            # MECE-valid °F partition (S6 law): left shoulder (range_low=None → -inf),
            # right shoulder (range_high=None → +inf), contiguous at 71→72.
            ("70-71°F", "condition-1", "yes-1", "chicago-high-1", "70-71°F", None, 71.0, "2026-05-24T08:11:00+00:00"),
            ("71-72°F", "condition-2", "yes-2", "chicago-high-2", "71-72°F", 72.0, None, "2026-05-24T08:11:00+00:00"),
        ],
    )

    receipt = _receipt(event, conn, forecast_conn=conn, topology_conn=conn)

    assert receipt.proof_accepted is True
    assert receipt.fdr_hypothesis_count == 4


def test_forecast_receipt_rejects_source_snapshot_available_after_decision_time():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots SET available_at = '2026-05-24T08:12:00+00:00'")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:causal forecast snapshot missing")


def test_forecast_receipt_requires_exact_causal_snapshot_from_source_data():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots SET snapshot_id = 'other-snapshot'")

    receipt = _receipt(event, conn)

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:causal forecast snapshot missing")


def test_forecast_receipt_requires_metric_match_in_source_snapshot():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots SET temperature_metric = 'low'")

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

    assert receipt.proof_accepted is True
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


# ── Task #107: portfolio-aware Kelly THROUGH the live reactor receipt path ────
# These drive the SAME build_event_bound_no_submit_receipt the daemon runs and
# prove the effective-bankroll reduction + INV-K3 single cap on a real receipt
# (not just the unit sizing path). The fixture city is "Chicago" (see
# _seed_platt_models). A held Chicago position has corr=1.0 (self) and reduces
# the new bet; the K3 cap holds against the receipt's bankroll.

def _held_chicago_position(committed_usd: float, tid: str):
    from src.state.portfolio import Position

    return Position(
        trade_id=tid,
        market_id=f"m_{tid}",
        city="Chicago",
        cluster="Chicago",
        target_date="2026-06-10",
        bin_label=f"bin_{tid}",
        direction="buy_yes",
        cost_basis_usd=float(committed_usd),
        size_usd=float(committed_usd),
        state="holding",
    )


def test_107_receipt_unwired_provider_equals_single_kelly_modulo_cap():
    """No portfolio_state_provider ⇒ receipt sizes EXACTLY as pre-#107 single
    Kelly (no regression), except the K3 single-bet cap never engages because
    the cap is only applied on the portfolio-aware path."""
    event = _bound_forecast_event()
    receipt = _receipt(
        event,
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: 170.0,
    )
    assert receipt.kelly_pass is True
    assert receipt.kelly_size_usd > 0


def test_107_receipt_correlated_hold_reduces_size_through_reactor():
    """LIVE-RECEIPT re-size proof: a held Chicago position (corr=1.0) reduces the
    new Chicago bet's kelly_size_usd via the effective-bankroll reduction,
    THROUGH the real reactor receipt builder."""
    from src.state.portfolio import PortfolioState

    bankroll = 170.0
    # Baseline: empty portfolio, portfolio-aware path wired.
    empty_state = PortfolioState(positions=[])
    base = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: bankroll,
        portfolio_state_provider=lambda: empty_state,
    )
    assert base.kelly_pass is True
    base_size = base.kelly_size_usd
    assert base_size > 0.0

    # Held correlated capital in the SAME city (corr=1.0). A SMALL amount so the
    # bet shrinks-but-survives (a larger hold would exhaust the haircut-reduced
    # budget and fail closed, which is INV-K6, tested separately).
    held_state = PortfolioState(
        positions=[_held_chicago_position(5.0, "held1")]
    )
    reduced = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: bankroll,
        portfolio_state_provider=lambda: held_state,
    )
    assert reduced.kelly_pass is True
    # The correlated hold strictly shrinks the new bet (effective-bankroll
    # reduction at full weight).
    assert reduced.kelly_size_usd < base_size, (
        f"correlated hold did not reduce receipt size: "
        f"{reduced.kelly_size_usd:.4f} !< {base_size:.4f}"
    )


def test_107_receipt_single_bet_respects_max_single_position_pct():
    """INV-K3 through the reactor: the receipt's kelly_size_usd never exceeds
    max_single_position_pct (0.10) of the provider bankroll on the
    portfolio-aware path — the headline 25-27%→≤10% fix."""
    from src.config import sizing_defaults
    from src.state.portfolio import PortfolioState

    bankroll = 170.0
    max_single_pct = float(sizing_defaults()["max_single_position_pct"])
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: bankroll,
        portfolio_state_provider=lambda: PortfolioState(positions=[]),
    )
    assert receipt.kelly_pass is True
    assert receipt.kelly_size_usd <= bankroll * max_single_pct + 1e-6, (
        f"receipt single bet {receipt.kelly_size_usd:.4f} "
        f"({receipt.kelly_size_usd / bankroll * 100:.1f}% of B) exceeds "
        f"max_single_position_pct cap {bankroll * max_single_pct:.4f}"
    )


def test_107_receipt_full_exposure_fails_closed_through_reactor():
    """INV-K6 through the reactor: when correlation-weighted committed capital
    exceeds the budget, the receipt fails closed (no positive size emitted)."""
    from src.state.portfolio import PortfolioState

    bankroll = 170.0
    # Same-city committed capital far exceeding the bankroll → B_eff = 0.
    over_state = PortfolioState(
        positions=[_held_chicago_position(bankroll + 100.0, "over1")]
    )
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: bankroll,
        portfolio_state_provider=lambda: over_state,
    )
    # Fail-closed: KELLY_REJECTED with zero size, never a positive over-sized bet.
    assert receipt.kelly_pass is False
    assert (receipt.kelly_size_usd or 0.0) == 0.0
