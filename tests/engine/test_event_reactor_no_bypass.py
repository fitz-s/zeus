# Created: 2026-05-24
# Last reused/audited: 2026-06-07
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
from src.contracts.execution_intent import DecisionSourceContext
from src.state.snapshot_repo import init_snapshot_schema
from src.engine.event_reactor_adapter import (
    build_event_bound_no_submit_receipt,
    edli_source_truth_gate,
    edli_trade_score_gate,
    executable_snapshot_gate_from_trade_conn,
    _durable_unmaterialized_live_cap_reservations,
    _seed_portfolio_reservations_from_durable_live_cap,
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
from src.sizing.portfolio_reservation import PortfolioReservationLedger
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
    tradeability_status_json: str = "{}",
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
        tradeability_status_json=tradeability_status_json,
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
            'COMPLETE', 0, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
            'SUCCESS', NULL
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
    decision_time = kwargs.pop("decision_time", DECISION_TIME)
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


def test_runtime_receipt_does_not_fit_platt_models(monkeypatch):
    def _forbid_runtime_fit(*_args, **_kwargs):
        raise AssertionError("receipt path must not call get_calibrator/runtime fit")

    monkeypatch.setattr("src.calibration.manager.get_calibrator", _forbid_runtime_fit)

    event = _bound_forecast_event()
    receipt = _receipt(event, _trade_conn_with_snapshot())

    assert receipt.proof_accepted is True
    assert receipt.decision_proof_bundle is not None
    assert receipt.decision_proof_bundle.calibration.payload["calibrator_model_key"] == "platt-world-1"


def test_forecast_trigger_event_without_q_or_token_fields_builds_no_submit_receipt():
    event = _forecast_event()
    receipt = _receipt(event, _trade_conn_with_snapshot(), decision_time=DECISION_TIME)

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


def test_latest_snapshot_rows_exclude_future_captured_rows_without_freshness_gate():
    from src.engine.event_reactor_adapter import _latest_snapshot_rows_for_event_family

    conn = _trade_conn_with_snapshot()
    cols = [str(row[1]) for row in conn.execute("PRAGMA table_info(executable_market_snapshots)").fetchall()]
    seed = dict(conn.execute("SELECT * FROM executable_market_snapshots WHERE condition_id = 'condition-1'").fetchone())
    seed["snapshot_id"] = "future-snapshot"
    seed["captured_at"] = "2026-05-24T08:13:00+00:00"
    seed["freshness_deadline"] = "2026-05-24T08:20:00+00:00"
    conn.execute(
        f"INSERT INTO executable_market_snapshots ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        [seed[col] for col in cols],
    )

    rows = _latest_snapshot_rows_for_event_family(
        conn,
        _forecast_event(),
        condition_ids=("condition-1",),
        fresh_at=DECISION_TIME,
        require_fresh=False,
    )

    assert rows
    assert "future-snapshot" not in {str(row.get("snapshot_id")) for row in rows}


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
    decision_context = DecisionSourceContext.from_forecast_context(forecast)
    assert decision_context is not None
    assert decision_context.source_id == "ecmwf_open_data"
    assert decision_context.model_family == "ecmwf_ens"
    assert decision_context.forecast_source_role == "entry_primary"
    assert decision_context.degradation_level == "OK"
    assert decision_context.authority_tier == "FORECAST"
    errors = set(decision_context.integrity_errors())
    assert "missing_raw_payload_hash" not in errors
    assert "missing_first_member_observed_time" not in errors
    assert "missing_run_complete_time" not in errors


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

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.submitted is False
    assert receipt.reason == "EVENT_BOUND_MARKET_TOPOLOGY_INVALID:market topology bin range missing"


def test_selected_snapshot_row_not_first_still_binds_matching_candidate(monkeypatch):
    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "0")
    event = _bound_forecast_event(token_id="yes-2")
    receipt = _receipt(event, _trade_conn_with_snapshot())

    assert receipt.condition_id == "condition-2"
    assert receipt.token_id == "yes-2"
    assert receipt.bin_label == "71-72°F"


def test_runtime_receipt_uses_selected_no_snapshot_not_yes_side_ask(monkeypatch):
    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "0")
    event = _bound_forecast_event(token_id="no-1")
    receipt = _receipt(event, _trade_conn_with_snapshot(selected_ask="0.10", no_selected_ask="0.80"))

    assert receipt.submitted is False
    assert receipt.token_id == "no-1"
    assert receipt.executable_snapshot_id == "snapshot-exec-1-no"
    assert receipt.c_fee_adjusted is not None
    assert receipt.c_fee_adjusted >= 0.80
    assert receipt.reason == "TRADE_SCORE_NON_POSITIVE"


def test_runtime_receipt_rejects_selected_no_when_only_yes_side_snapshot_exists(monkeypatch):
    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "0")
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
            '2026-05-26T00:00:00+00:00', '2026-05-24T08:12:30+00:00', 1, 0,
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

    receipt = _receipt(event, conn, decision_time=datetime(2026, 5, 24, 8, 13, tzinfo=timezone.utc))

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

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

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


def test_missing_platt_bucket_uses_identity_fallback_authority():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots SET p_cal_json = NULL")
    calibration_conn = _calibration_conn_with_platt_model()
    calibration_conn.execute("DELETE FROM platt_models")

    receipt = _receipt(event, conn, calibration_conn=calibration_conn)

    assert receipt.proof_accepted is True
    assert receipt.reason == "event_bound_final_intent_no_submit"
    calibration = receipt.decision_proof_bundle.calibration.payload
    assert calibration["authority"] == "IDENTITY_FALLBACK_NO_PLATT_BUCKET"
    assert calibration["calibrator_model_key"].startswith("identity_fallback_no_platt_bucket_v1:")
    assert receipt.decision_proof_bundle.belief.payload["calibrator_model_key"] == calibration["calibrator_model_key"]


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

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

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

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

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


def test_forecast_authority_resolver_prefers_attached_forecasts():
    from src.engine.event_reactor_adapter import _authority_table_ref

    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS forecasts")
    conn.execute("CREATE TABLE forecasts.ensemble_snapshots (snapshot_id TEXT PRIMARY KEY)")

    assert _authority_table_ref(conn, "ensemble_snapshots") == "forecasts.ensemble_snapshots"


def test_snapshot_lead_days_falls_back_to_source_available_and_local_day_start():
    from src.engine.event_reactor_adapter import _snapshot_lead_days

    lead_days = _snapshot_lead_days(
        snapshot={
            "source_available_at": "2026-06-05T12:00:00+00:00",
            "local_day_start_utc": "2026-06-06T22:00:00+00:00",
        },
        family=SimpleNamespace(target_date="2026-06-07"),
        payload={},
    )

    assert lead_days == pytest.approx(34.0 / 24.0)


def test_snapshot_lead_days_falls_back_to_day0_observation_time():
    from src.engine.event_reactor_adapter import _snapshot_lead_days

    lead_days = _snapshot_lead_days(
        snapshot={},
        family=SimpleNamespace(target_date="2026-06-06"),
        payload={
            "observation_time": "2026-06-06T04:00:00+00:00",
            "observation_available_at": "2026-06-06T05:15:17.901309+00:00",
        },
    )

    assert lead_days == 0.0


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

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.submitted is False
    assert "FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:READER_TEST_BLOCK" in receipt.reason


def test_forecast_reader_revalidation_uses_reactor_decision_time(monkeypatch):
    from src.data import executable_forecast_reader
    from src.data.executable_forecast_reader import (
        ExecutableForecastBundle,
        ExecutableForecastBundleResult,
        ExecutableForecastEvidence,
        ExecutableForecastSnapshot,
    )

    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    decision_time = datetime(2026, 5, 24, 8, 17, tzinfo=timezone.utc)
    captured = []
    evidence = ExecutableForecastEvidence(
        forecast_source_id="ecmwf_open_data",
        forecast_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        source_transport="ensemble_snapshots_db_reader",
        source_run_id="run-1",
        release_calendar_key="ecmwf_open_data",
        coverage_id="coverage-1",
        producer_readiness_id="producer_readiness:coverage-1",
        entry_readiness_id=None,
        source_cycle_time="2026-05-24T00:00:00+00:00",
        source_issue_time="2026-05-24T00:00:00+00:00",
        source_release_time="2026-05-24T07:00:00+00:00",
        source_available_at="2026-05-24T08:10:00+00:00",
        fetch_started_at="2026-05-24T07:10:00+00:00",
        fetch_finished_at="2026-05-24T08:05:00+00:00",
        captured_at="2026-05-24T08:10:00+00:00",
        input_snapshot_ids=(1,),
        raw_payload_hash="hash-raw",
        manifest_hash="hash-manifest",
        target_local_date="2026-05-25",
        target_window_start_utc="2026-05-25T05:00:00+00:00",
        target_window_end_utc="2026-05-26T05:00:00+00:00",
        city_timezone="America/Chicago",
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
    )
    snapshot = ExecutableForecastSnapshot(
        snapshot_id=1,
        city="Chicago",
        target_local_date=datetime(2026, 5, 25, tzinfo=timezone.utc).date(),
        temperature_metric="high",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        members=tuple(float(60 + (index % 10)) for index in range(51)),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_db_reader",
        source_run_id="run-1",
        release_calendar_key="ecmwf_open_data",
        source_cycle_time="2026-05-24T00:00:00+00:00",
        source_release_time="2026-05-24T07:00:00+00:00",
        source_available_at="2026-05-24T08:10:00+00:00",
        issue_time="2026-05-24T00:00:00+00:00",
        valid_time="2026-05-25",
        available_at="2026-05-24T08:10:00+00:00",
        fetch_time="2026-05-24T08:10:00+00:00",
        manifest_hash="hash-manifest",
        members_unit="degF",
        local_day_start_utc="2026-05-25T05:00:00+00:00",
        step_horizon_hours=32.0,
        first_member_observed_time="2026-05-24T07:10:00+00:00",
        run_complete_time="2026-05-24T08:05:00+00:00",
        raw_orderbook_hash_transition_delta_ms=50,
    )

    monkeypatch.setattr(
        executable_forecast_reader,
        "read_executable_forecast",
        lambda *_args, **kwargs: captured.append(kwargs["decision_time"])
        or ExecutableForecastBundleResult(
            status="LIVE_ELIGIBLE",
            bundle=ExecutableForecastBundle(snapshot=snapshot, evidence=evidence),
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


def test_price_stale_selected_snapshot_blocks_shadow_receipt_before_scoring():
    """Market identity persists, but shadow will-trade cannot score stale selected-bin price."""
    event = _bound_forecast_event()
    # captured_at before freshness_deadline (invariant: deadline >= captured);
    # freshness_deadline is before decision_time (08:12) — simulates price-stale snapshot.
    conn = _trade_conn_with_snapshot(
        captured_at="2026-05-24T08:10:00+00:00",
        freshness_deadline="2026-05-24T08:11:59+00:00",
    )

    receipt = _receipt(event, conn, decision_time=datetime(2026, 5, 24, 8, 12, tzinfo=timezone.utc))

    assert receipt.submitted is False
    assert receipt.reason is not None
    assert receipt.reason.startswith("EXECUTABLE_SNAPSHOT_STALE:")


def test_capital_efficiency_allows_high_price_positive_ev_for_ranking():
    from src.contracts.execution_price import ExecutionPrice
    from src.engine.event_reactor_adapter import _capital_efficiency_untradeable_reason

    reason = _capital_efficiency_untradeable_reason(
        execution_price=ExecutionPrice(
            0.98196,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_lcb_5pct=0.99,
        trade_score=0.00553868962634317,
    )

    assert reason is None


def test_capital_efficiency_allows_strong_after_cost_roi_new_market():
    from src.contracts.execution_price import ExecutionPrice
    from src.engine.event_reactor_adapter import _capital_efficiency_untradeable_reason

    assert _capital_efficiency_untradeable_reason(
        execution_price=ExecutionPrice(
            0.75924,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_lcb_5pct=0.86,
        trade_score=0.0537892625895399,
    ) is None


def test_capital_efficiency_default_production_gate_allows_positive_ev(monkeypatch):
    from src.contracts.execution_price import ExecutionPrice
    from src.engine.event_reactor_adapter import _capital_efficiency_untradeable_reason

    reason = _capital_efficiency_untradeable_reason(
        execution_price=ExecutionPrice(
            0.98196,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_lcb_5pct=0.99,
        trade_score=0.00553868962634317,
    )

    assert reason is None


def test_capital_efficiency_allows_high_price_micro_upside_for_sizing():
    from src.contracts.execution_price import ExecutionPrice
    from src.engine.event_reactor_adapter import _capital_efficiency_untradeable_reason

    reason = _capital_efficiency_untradeable_reason(
        execution_price=ExecutionPrice(
            0.97,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_lcb_5pct=0.99,
        trade_score=0.019758898884025,
    )

    assert reason is None


def test_native_costs_use_token_side_snapshot_rows_not_first_condition_row():
    from src.engine import event_reactor_adapter as adapter

    candidate = SimpleNamespace(
        condition_id="condition-1",
        yes_token_id="yes-token",
        no_token_id="no-token",
    )
    family = SimpleNamespace(candidates=(candidate,))
    no_row = {
        "condition_id": "condition-1",
        "snapshot_id": "no-snapshot",
        "yes_token_id": "yes-token",
        "no_token_id": "no-token",
        "selected_outcome_token_id": "no-token",
        "outcome_label": "NO",
        "orderbook_top_ask": "0.82",
        "orderbook_top_bid": "0.78",
        "depth_at_best_ask": 25,
        "min_tick_size": "0.01",
        "min_order_size": "5",
        "fee_details_json": json.dumps({"fee_rate_fraction": 0.0}),
        "orderbook_depth_json": "{}",
    }
    yes_row = {
        **no_row,
        "snapshot_id": "yes-snapshot",
        "selected_outcome_token_id": "yes-token",
        "outcome_label": "YES",
        "orderbook_top_ask": "0.18",
        "orderbook_top_bid": "0.14",
    }

    costs = adapter._native_costs_by_candidate_direction(family, [no_row, yes_row])

    assert costs[("condition-1", "buy_yes")][1].value == pytest.approx(0.18)
    assert costs[("condition-1", "buy_no")][1].value == pytest.approx(0.82)


def test_selection_prefers_lcb_kelly_growth_not_modal_adjacent_no(monkeypatch):
    from src.contracts.execution_price import ExecutionPrice
    from src.engine.event_reactor_adapter import _CandidateProof, _selected_candidate_proof

    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "1")

    modal_adjacent_no = _CandidateProof(
        candidate=SimpleNamespace(condition_id="helsinki-22c"),
        token_id="helsinki-22c-no-token",
        direction="buy_no",
        row={"condition_id": "helsinki-22c"},
        executable_snapshot_id="helsinki-22c-snapshot",
        execution_price=ExecutionPrice(
            0.70,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.80,
        q_lcb_5pct=0.72,
        c_cost_95pct=0.71,
        p_fill_lcb=0.90,
        trade_score=0.020,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="pcal",
        p_live_vector_hash="plive",
    )
    better_family_trade = replace(
        modal_adjacent_no,
        candidate=SimpleNamespace(condition_id="helsinki-23c"),
        token_id="helsinki-23c-yes-token",
        direction="buy_yes",
        row={"condition_id": "helsinki-23c"},
        executable_snapshot_id="helsinki-23c-snapshot",
        execution_price=ExecutionPrice(
            0.30,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.62,
        q_lcb_5pct=0.58,
        c_cost_95pct=0.31,
        trade_score=0.040,
    )

    selected = _selected_candidate_proof({}, (modal_adjacent_no, better_family_trade))

    assert selected is better_family_trade


def test_selector_enabled_does_not_fallback_to_low_win_rate_positive_ev(monkeypatch):
    from src.contracts.execution_price import ExecutionPrice
    from src.engine.event_reactor_adapter import _CandidateProof, _selected_candidate_proof

    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "1")

    low_win_rate_lottery = _CandidateProof(
        candidate=SimpleNamespace(condition_id="cheap-tail"),
        token_id="cheap-tail-yes-token",
        direction="buy_yes",
        row={"condition_id": "cheap-tail"},
        executable_snapshot_id="cheap-tail-snapshot",
        execution_price=ExecutionPrice(
            0.01,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.08,
        q_lcb_5pct=0.42,
        c_cost_95pct=0.011,
        p_fill_lcb=0.90,
        trade_score=0.040,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="pcal",
        p_live_vector_hash="plive",
    )

    selected = _selected_candidate_proof({}, (low_win_rate_lottery,))

    assert selected is None


def test_replacement_live_authority_direction_rebinds_to_sibling_proof():
    from src.contracts.execution_price import ExecutionPrice
    from src.engine.event_reactor_adapter import (
        _CandidateProof,
        _replacement_live_authority_proof_for_direction,
    )

    candidate = SimpleNamespace(condition_id="condition-1")
    buy_yes = _CandidateProof(
        candidate=candidate,
        token_id="yes-1",
        direction="buy_yes",
        row={"condition_id": "condition-1"},
        executable_snapshot_id="snapshot-yes",
        execution_price=ExecutionPrice(
            0.40,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.80,
        q_lcb_5pct=0.72,
        c_cost_95pct=0.41,
        p_fill_lcb=0.90,
        trade_score=0.10,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="pcal",
        p_live_vector_hash="plive",
    )
    buy_no = replace(
        buy_yes,
        token_id="no-1",
        direction="buy_no",
        executable_snapshot_id="snapshot-no",
        execution_price=ExecutionPrice(
            0.30,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.35,
        q_lcb_5pct=0.31,
        c_cost_95pct=0.31,
    )

    selected = _replacement_live_authority_proof_for_direction(
        proofs=(buy_yes, buy_no),
        baseline_proof=buy_yes,
        effective_direction="buy_no",
    )

    assert selected is buy_no
    assert selected.token_id == "no-1"
    assert selected.executable_snapshot_id == "snapshot-no"


def test_replacement_live_authority_same_direction_replaces_receipt_probability(monkeypatch):
    from src.engine.replacement_forecast_reactor_hook import ReplacementForecastReactorHookResult

    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "1")

    class _ReplacementProvenance:
        def as_dict(self):
            return {
                "trade_authority_status": "LIVE_AUTHORITY",
                "source_id": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
            }

    def _live_authority_hook(proof, event, decision_time):
        return ReplacementForecastReactorHookResult(
            status="LIVE_AUTHORITY",
            reason_codes=("test-live-authority",),
            effective_direction=proof.direction,
            effective_q_posterior=0.82,
            effective_q_lcb=0.79,
            effective_kelly_fraction=0.0,
            receipt_provenance=_ReplacementProvenance(),
        )

    receipt = _receipt(
        _bound_forecast_event(token_id="yes-1"),
        _trade_conn_with_snapshot(selected_ask="0.40", no_selected_ask="0.80"),
        replacement_forecast_hook=_live_authority_hook,
    )

    assert receipt.proof_accepted is True
    assert receipt.token_id == "yes-1"
    assert receipt.direction == "buy_yes"
    assert receipt.q_live == pytest.approx(0.82)
    assert receipt.q_lcb_5pct == pytest.approx(0.79)
    assert receipt.trade_score is not None
    assert receipt.trade_score > 0.0
    assert receipt.replacement_forecast is not None
    assert receipt.replacement_forecast["trade_authority_status"] == "LIVE_AUTHORITY"


def test_token_redecision_refresh_scope_does_not_force_requested_token(monkeypatch):
    from src.contracts.execution_price import ExecutionPrice
    from src.engine.event_reactor_adapter import _CandidateProof, _selected_candidate_proof

    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "1")

    requested_token = _CandidateProof(
        candidate=SimpleNamespace(condition_id="expensive"),
        token_id="requested-token",
        direction="buy_no",
        row={"condition_id": "expensive"},
        executable_snapshot_id="expensive-snapshot",
        execution_price=ExecutionPrice(
            0.99,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.999,
        q_lcb_5pct=0.99,
        c_cost_95pct=0.991,
        p_fill_lcb=0.90,
        trade_score=0.010,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="pcal",
        p_live_vector_hash="plive",
    )
    sibling = replace(
        requested_token,
        candidate=SimpleNamespace(condition_id="sibling"),
        token_id="sibling-token",
        row={"condition_id": "sibling"},
        executable_snapshot_id="sibling-snapshot",
        execution_price=ExecutionPrice(
            0.20,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.60,
        q_lcb_5pct=0.55,
        c_cost_95pct=0.21,
        trade_score=0.020,
    )

    selected = _selected_candidate_proof(
        {"token_id": "requested-token", "condition_id": "expensive"},
        (requested_token, sibling),
    )

    assert selected is sibling


def test_opportunity_book_selector_is_default_on_for_requested_token(monkeypatch):
    from src.config import settings
    from src.contracts.execution_price import ExecutionPrice
    from src.engine.event_reactor_adapter import _CandidateProof, _selected_candidate_proof

    monkeypatch.delenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", raising=False)
    edli = dict(settings._data["edli_v1"])
    edli.pop("opportunity_book_selector_enabled", None)
    monkeypatch.setitem(settings._data, "edli_v1", edli)

    requested_bin = _CandidateProof(
        candidate=SimpleNamespace(condition_id="helsinki-22c"),
        token_id="requested-22c-no-token",
        direction="buy_no",
        row={"condition_id": "helsinki-22c"},
        executable_snapshot_id="requested-snapshot",
        execution_price=ExecutionPrice(
            0.70,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.80,
        q_lcb_5pct=0.72,
        c_cost_95pct=0.71,
        p_fill_lcb=0.90,
        trade_score=0.020,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="pcal",
        p_live_vector_hash="plive",
    )
    better_sibling = replace(
        requested_bin,
        candidate=SimpleNamespace(condition_id="helsinki-23c"),
        token_id="sibling-23c-no-token",
        row={"condition_id": "helsinki-23c"},
        executable_snapshot_id="sibling-snapshot",
        execution_price=ExecutionPrice(
            0.72,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.92,
        q_lcb_5pct=0.84,
        c_cost_95pct=0.73,
        trade_score=0.050,
    )

    selected = _selected_candidate_proof(
        {"token_id": "requested-22c-no-token", "condition_id": "helsinki-22c"},
        (requested_bin, better_sibling),
    )

    assert selected is better_sibling


def test_family_selector_keeps_stale_sibling_price_for_pre_submit_comparison(monkeypatch):
    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "1")
    event = _bound_forecast_event(token_id="yes-1")
    conn = _trade_conn_with_snapshot(
        selected_ask="0.70",
        condition_count=2,
        snapshot_condition_count=2,
        freshness_deadline="2026-05-24T08:11:00+00:00",
        captured_at="2026-05-24T08:10:00+00:00",
    )
    cols = [str(row[1]) for row in conn.execute("PRAGMA table_info(executable_market_snapshots)").fetchall()]
    for seed_id, fresh_id in (
        ("snapshot-exec-1", "snapshot-exec-1-fresh"),
        ("snapshot-exec-1-no", "snapshot-exec-1-no-fresh"),
    ):
        seed = dict(
            conn.execute(
                "SELECT * FROM executable_market_snapshots WHERE snapshot_id = ?",
                (seed_id,),
            ).fetchone()
        )
        seed["snapshot_id"] = fresh_id
        seed["captured_at"] = "2026-05-24T08:12:00+00:00"
        seed["freshness_deadline"] = "2026-05-25T00:00:00+00:00"
        conn.execute(
            f"INSERT INTO executable_market_snapshots ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            [seed.get(col) for col in cols],
        )

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.proof_accepted is True
    assert receipt.opportunity_book is not None
    book = receipt.opportunity_book
    assert book["selected_candidate_id"] == book["actual_receipt_selected_candidate_id"]
    selected_id = book["selected_candidate_id"]
    selected = next(c for c in book["candidates"] if c["candidate_id"] == selected_id)
    assert selected["admitted"] is True
    condition_2_candidates = [c for c in book["candidates"] if c["condition_id"] == "condition-2"]
    assert condition_2_candidates
    assert any(c["direction"] == "buy_no" for c in condition_2_candidates)
    assert not str(book["loser_reasons"].get(selected_id, "")).startswith("EXECUTABLE_SNAPSHOT_STALE")
    for candidate in condition_2_candidates:
        reason = str(book["loser_reasons"].get(candidate["candidate_id"], ""))
        assert not reason.startswith("EXECUTABLE_SNAPSHOT_STALE")


def test_opportunity_book_selector_settings_false_fails_closed(monkeypatch):
    from src.config import settings
    from src.contracts.execution_price import ExecutionPrice
    from src.engine.event_reactor_adapter import _CandidateProof, _selected_candidate_proof

    monkeypatch.delenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", raising=False)
    edli = dict(settings._data["edli_v1"])
    edli["opportunity_book_selector_enabled"] = "false"
    monkeypatch.setitem(settings._data, "edli_v1", edli)

    requested_bin = _CandidateProof(
        candidate=SimpleNamespace(condition_id="requested"),
        token_id="requested-token",
        direction="buy_no",
        row={"condition_id": "requested"},
        executable_snapshot_id="requested-snapshot",
        execution_price=ExecutionPrice(
            0.70,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.80,
        q_lcb_5pct=0.72,
        c_cost_95pct=0.71,
        p_fill_lcb=0.90,
        trade_score=0.020,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="pcal",
        p_live_vector_hash="plive",
    )
    better_sibling = replace(
        requested_bin,
        candidate=SimpleNamespace(condition_id="sibling"),
        token_id="sibling-token",
        row={"condition_id": "sibling"},
        executable_snapshot_id="sibling-snapshot",
        trade_score=0.050,
    )

    selected = _selected_candidate_proof(
        {"token_id": "requested-token", "condition_id": "requested"},
        (requested_bin, better_sibling),
    )

    assert selected is None


def test_opportunity_book_selector_excludes_limit_untradeable_candidate(monkeypatch):
    from src.contracts.execution_price import ExecutionPrice
    from src.engine.event_reactor_adapter import (
        _CandidateProof,
        _opportunity_book_from_proofs,
        _selected_candidate_proof,
    )

    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "1")

    high_score_below_tick = _CandidateProof(
        candidate=SimpleNamespace(condition_id="below-tick"),
        token_id="below-tick-token",
        direction="buy_yes",
        row={"condition_id": "below-tick", "min_tick_size": "0.05"},
        executable_snapshot_id="below-tick-snapshot",
        execution_price=ExecutionPrice(
            0.01,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.90,
        q_lcb_5pct=0.85,
        c_cost_95pct=0.011,
        p_fill_lcb=0.90,
        trade_score=0.50,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="pcal",
        p_live_vector_hash="plive",
    )
    admitted = replace(
        high_score_below_tick,
        candidate=SimpleNamespace(condition_id="admitted"),
        token_id="admitted-token",
        row={"condition_id": "admitted", "min_tick_size": "0.01"},
        executable_snapshot_id="admitted-snapshot",
        execution_price=ExecutionPrice(
            0.20,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.60,
        q_lcb_5pct=0.55,
        c_cost_95pct=0.21,
        trade_score=0.02,
    )

    selected = _selected_candidate_proof({}, (high_score_below_tick, admitted))
    book = _opportunity_book_from_proofs(
        event_id="event-1",
        family_id="family-1",
        proofs=(high_score_below_tick, admitted),
        selected_proof=selected,
    ).to_receipt_dict()

    assert selected is admitted
    assert book["selected_candidate_id"] == book["actual_receipt_selected_candidate_id"]
    assert book["proposed_selected_candidate_id"] == book["actual_receipt_selected_candidate_id"]
    loser_reason = next(iter(book["loser_reasons"].values()))
    assert loser_reason.startswith("EXECUTION_PRICE_BELOW_MIN_TICK:")


def test_live_authority_rejects_receipt_token_that_is_not_book_selected():
    from src.engine.event_reactor_adapter import _assert_event_bound_receipt_live_authority
    from src.events.reactor import EventSubmissionReceipt

    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="event-helsinki",
        condition_id="cond-22",
        token_id="no-22",
        direction="buy_no",
        q_source="emos",
        opportunity_book={
            "selected_candidate_id": "cand-23-yes",
            "actual_receipt_selected_candidate_id": "cand-23-yes",
            "candidates": [
                {
                    "candidate_id": "cand-22-no",
                    "condition_id": "cond-22",
                    "token_id": "no-22",
                    "direction": "buy_no",
                },
                {
                    "candidate_id": "cand-23-yes",
                    "condition_id": "cond-23",
                    "token_id": "yes-23",
                    "direction": "buy_yes",
                },
            ],
        },
    )

    with pytest.raises(ValueError, match="EDLI_LIVE_OPPORTUNITY_BOOK_RECEIPT_NOT_SELECTED"):
        _assert_event_bound_receipt_live_authority(receipt)


def test_live_authority_accepts_receipt_token_bound_to_book_selection():
    from src.engine.event_reactor_adapter import _assert_event_bound_receipt_live_authority
    from src.events.reactor import EventSubmissionReceipt

    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="event-helsinki",
        condition_id="cond-23",
        token_id="yes-23",
        direction="buy_yes",
        q_source="emos",
        opportunity_book={
            "selected_candidate_id": "cand-23-yes",
            "actual_receipt_selected_candidate_id": "cand-23-yes",
            "candidates": [
                {
                    "candidate_id": "cand-23-yes",
                    "condition_id": "cond-23",
                    "token_id": "yes-23",
                    "direction": "buy_yes",
                },
            ],
        },
    )

    _assert_event_bound_receipt_live_authority(receipt)


def test_candidate_low_volume_preserves_zero_volume_usd():
    from src.engine import event_reactor_adapter as adapter

    assert adapter._candidate_low_volume_usd(
        {"volume_usd": 0.0, "volume": 25.0, "total_volume": 50.0}
    ) == 0.0


def test_opportunity_book_selector_excludes_all_locked_executables(monkeypatch):
    from src.contracts.execution_price import ExecutionPrice
    from src.engine import event_reactor_adapter as adapter

    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "1")
    monkeypatch.setattr(
        adapter,
        "_locked_candidate_no_price_improvement_reason",
        lambda _conn, proof: "LOCKED_OPPORTUNITY_NO_PRICE_IMPROVEMENT"
        if proof.execution_price is not None
        else None,
    )

    locked = adapter._CandidateProof(
        candidate=SimpleNamespace(condition_id="locked"),
        token_id="locked-token",
        direction="buy_no",
        row={"condition_id": "locked"},
        executable_snapshot_id="locked-snapshot",
        execution_price=ExecutionPrice(
            0.20,
            "ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        q_posterior=0.80,
        q_lcb_5pct=0.75,
        c_cost_95pct=0.21,
        p_fill_lcb=0.90,
        trade_score=0.50,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="pcal",
        p_live_vector_hash="plive",
    )
    non_executable_fallback = replace(
        locked,
        candidate=SimpleNamespace(condition_id="fallback"),
        token_id="fallback-token",
        row=None,
        executable_snapshot_id=None,
        execution_price=None,
        q_posterior=0.70,
        q_lcb_5pct=0.90,
        c_cost_95pct=None,
        trade_score=0.0,
        passed_prefilter=False,
        native_quote_available=False,
        missing_reason="missing executable snapshot row",
    )

    selected = adapter._selected_candidate_proof(
        {},
        (locked, non_executable_fallback),
        locked_opportunity_conn=sqlite3.connect(":memory:"),
    )
    book = adapter._opportunity_book_from_proofs(
        event_id="event-1",
        family_id="family-1",
        proofs=(locked, non_executable_fallback),
        selected_proof=selected,
        locked_opportunity_conn=sqlite3.connect(":memory:"),
    ).to_receipt_dict()

    assert selected is non_executable_fallback
    assert book["proposed_selected_candidate_id"] is None
    assert "LOCKED_OPPORTUNITY_NO_PRICE_IMPROVEMENT" in book["loser_reasons"].values()


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


def test_top_ask_without_depth_does_not_create_fillable_quote(monkeypatch):
    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "0")
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot(selected_ask="0.40", depth_json="{}")

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.submitted is False
    assert receipt.reason.startswith("EXECUTABLE_NATIVE_ASK_MISSING")
    assert receipt.native_quote_available is False


def test_non_executable_snapshot_with_depth_cannot_create_fillable_quote():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot(
        selected_ask="0.40",
        tradeability_status_json=json.dumps(
            {"executable_allowed": False, "reason": "synthetic_clob_market_info_substrate_only"}
        ),
    )

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

    assert receipt.submitted is False
    assert receipt.reason.startswith("EXECUTABLE_NATIVE_ASK_MISSING")
    assert "synthetic_clob_market_info_substrate_only" in receipt.reason
    assert receipt.proof_accepted is False
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

    receipt = _receipt(event, conn, decision_time=DECISION_TIME)

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


def test_runtime_bankroll_for_sizing_uses_spendable_cash_not_equity(monkeypatch):
    from src.engine.event_reactor_adapter import _runtime_bankroll_usd
    from src.runtime import bankroll_provider
    from src.runtime.bankroll_provider import BankrollOfRecord

    monkeypatch.setattr(
        bankroll_provider,
        "cached",
        lambda **_kwargs: BankrollOfRecord(
            value_usd=177.3,
            spendable_cash_usd=90.0,
            fetched_at="2026-06-07T00:00:00+00:00",
        ),
    )

    assert _runtime_bankroll_usd(cached_only=True) == pytest.approx(90.0)


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
    conn = _trade_conn_with_snapshot(captured_at="2026-05-24T08:10:00+00:00")
    conn.execute("UPDATE ensemble_snapshots SET available_at = '2026-05-24T08:12:00+00:00'")

    receipt = _receipt(event, conn, decision_time=datetime.fromisoformat(event.received_at))

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

    receipt = _receipt(event, conn, decision_time=datetime.fromisoformat(event.received_at))

    assert receipt.proof_accepted is True
    assert receipt.condition_id == "condition-2"
    assert receipt.token_id == "yes-2"
    assert receipt.q_live is not None
    assert receipt.q_live > 0.99
    assert receipt.fdr_hypothesis_count == 4
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_runtime_receipt_rejects_missing_native_ask_instead_of_defaulting_midpoint(monkeypatch):
    monkeypatch.setenv("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "0")
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


def test_107_receipt_fractional_kelly_is_not_single_position_clipped():
    """The reactor carries fractional Kelly size without a single-position clip."""
    from src.state.portfolio import PortfolioState

    bankroll = 170.0
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: bankroll,
        portfolio_state_provider=lambda: PortfolioState(positions=[]),
    )
    assert receipt.kelly_pass is True
    assert receipt.kelly_size_usd is not None
    assert receipt.kelly_size_usd > 0.0


def test_107_receipt_full_exposure_soft_damps_through_reactor():
    """Existing exposure should shrink the marginal Kelly size, not hard-zero it."""
    from src.state.portfolio import PortfolioState

    bankroll = 170.0
    base = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: bankroll,
        portfolio_state_provider=lambda: PortfolioState(positions=[]),
    )
    assert base.kelly_pass is True
    assert base.kelly_size_usd is not None

    # Same-city committed capital far exceeding the bankroll creates high
    # portfolio pressure, but the marginal positive-edge proof still flows.
    over_state = PortfolioState(
        positions=[_held_chicago_position(bankroll + 100.0, "over1")]
    )
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: bankroll,
        portfolio_state_provider=lambda: over_state,
    )
    assert receipt.kelly_pass is True
    assert receipt.kelly_size_usd is not None
    assert 0.0 < receipt.kelly_size_usd < base.kelly_size_usd


def _live_cap_seed_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE edli_live_cap_usage (
            usage_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            decision_time TEXT,
            cap_scope TEXT,
            max_notional_usd REAL,
            max_orders_per_day INTEGER,
            reserved_notional_usd REAL NOT NULL,
            order_count INTEGER,
            reservation_status TEXT NOT NULL,
            final_intent_id TEXT,
            execution_command_id TEXT,
            created_at TEXT,
            schema_version INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE edli_live_order_events (
            aggregate_event_id TEXT PRIMARY KEY,
            aggregate_id TEXT NOT NULL,
            event_sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            parent_event_hash TEXT,
            event_hash TEXT,
            payload_json TEXT NOT NULL,
            payload_hash TEXT,
            source_authority TEXT,
            occurred_at TEXT,
            created_at TEXT,
            schema_version INTEGER
        )
        """
    )
    return conn


def _insert_live_cap_usage(
    conn: sqlite3.Connection,
    *,
    usage_id: str,
    event_id: str,
    final_intent_id: str,
    usd: float,
    status: str = "CONSUMED",
    execution_command_id: str = "cmd",
) -> None:
    conn.execute(
        """
        INSERT INTO edli_live_cap_usage (
            usage_id, event_id, decision_time, cap_scope, max_notional_usd,
            max_orders_per_day, reserved_notional_usd, order_count,
            reservation_status, final_intent_id, execution_command_id,
            created_at, schema_version
        )
        VALUES (?, ?, '2026-06-07T00:00:00+00:00', 'tiny_live_canary',
                100.0, 99, ?, 1, ?, ?, ?, '2026-06-07T00:00:00+00:00', 1)
        """,
        (usage_id, event_id, usd, status, final_intent_id, execution_command_id),
    )


def _insert_live_order_event(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    seq: int,
    event_type: str,
    payload: dict[str, object],
) -> None:
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        )
        VALUES (?, ?, ?, ?, NULL, ?, ?, 'payload-hash', 'test',
                '2026-06-07T00:00:00+00:00', '2026-06-07T00:00:00+00:00', 1)
        """,
        (
            f"{aggregate_id}:{seq}:{event_type}",
            aggregate_id,
            seq,
            event_type,
            f"hash-{aggregate_id}-{seq}",
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        ),
    )


def test_107_durable_live_cap_seed_counts_only_unmaterialized_live_exposure():
    """Cross-cycle capital seed is universal: count submitted live-cap notional
    only until position truth or authenticated absence/release proves it gone."""
    conn = _live_cap_seed_conn()
    cases = [
        ("usage-pending", "event-pending", "intent-pending", 12.5, "CONSUMED", "Chicago"),
        ("usage-reserved", "event-reserved", "intent-reserved", 3.0, "RESERVED", "Berlin"),
        ("usage-filled", "event-filled", "intent-filled", 9.0, "CONSUMED", "Tokyo"),
        ("usage-matched", "event-matched", "intent-matched", 6.0, "CONSUMED", "Paris"),
        ("usage-released", "event-released", "intent-released", 7.0, "CONSUMED", "London"),
        ("usage-absent", "event-absent", "intent-absent", 4.0, "CONSUMED", "Madrid"),
    ]
    for usage_id, event_id, final_intent_id, usd, status, city in cases:
        aggregate_id = f"{event_id}:{final_intent_id}"
        _insert_live_cap_usage(
            conn,
            usage_id=usage_id,
            event_id=event_id,
            final_intent_id=final_intent_id,
            usd=usd,
            status=status,
        )
        _insert_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            seq=1,
            event_type="PreSubmitRevalidated",
            payload={"city": city},
        )

    _insert_live_order_event(
        conn,
        aggregate_id="event-filled:intent-filled",
        seq=2,
        event_type="UserTradeObserved",
        payload={"fill_id": "fill-1", "fill_authority_state": "FILL_CONFIRMED"},
    )
    _insert_live_order_event(
        conn,
        aggregate_id="event-matched:intent-matched",
        seq=2,
        event_type="UserTradeObserved",
        payload={"fill_id": "fill-2", "fill_authority_state": "MATCHED_PENDING_FINALITY"},
    )
    _insert_live_order_event(
        conn,
        aggregate_id="event-released:intent-released",
        seq=2,
        event_type="Reconciled",
        payload={"cap_transition_recommendation": "RELEASED"},
    )
    _insert_live_order_event(
        conn,
        aggregate_id="event-absent:intent-absent",
        seq=2,
        event_type="Reconciled",
        payload={"authenticated_absence_proof": {"checked": True}},
    )

    rows = _durable_unmaterialized_live_cap_reservations(conn)
    assert rows == (
        ("durable_live_cap:usage-matched", "Paris", pytest.approx(6.0)),
        ("durable_live_cap:usage-pending", "Chicago", pytest.approx(12.5)),
        ("durable_live_cap:usage-reserved", "Berlin", pytest.approx(3.0)),
    )


def test_107_durable_live_cap_seed_excludes_trade_truth_materialized_exposure():
    """Live-cap seed must not double-count orders already terminal or materialized.

    Regression shape from live: world.edli_live_order_events may miss the
    UserTradeObserved leg while zeus_trades.db already proves command FILLED or
    position_current active. Those rows are no longer in-flight capital.
    """

    conn = _live_cap_seed_conn()
    trade_conn = sqlite3.connect(":memory:")
    trade_conn.row_factory = sqlite3.Row
    trade_conn.executescript(
        """
        CREATE TABLE venue_commands (
            decision_id TEXT NOT NULL,
            intent_kind TEXT NOT NULL,
            state TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE position_current (
            phase TEXT NOT NULL,
            token_id TEXT,
            no_token_id TEXT,
            cost_basis_usd REAL,
            chain_cost_basis_usd REAL,
            shares REAL
        );
        """
    )
    cases = [
        ("usage-filled-command", "event-filled-command", "intent:token-filled-command", 10.0, "cmd-filled", "Madrid"),
        ("usage-active-position", "event-active-position", "intent:token-active-position", 4.0, "cmd-active", "Wellington"),
        ("usage-still-inflight", "event-still-inflight", "intent:token-still-inflight", 6.0, "cmd-open", "Chicago"),
    ]
    for usage_id, event_id, final_intent_id, usd, command_id, city in cases:
        _insert_live_cap_usage(
            conn,
            usage_id=usage_id,
            event_id=event_id,
            final_intent_id=final_intent_id,
            usd=usd,
            execution_command_id=command_id,
        )
        _insert_live_order_event(
            conn,
            aggregate_id=f"{event_id}:{final_intent_id}",
            seq=1,
            event_type="PreSubmitRevalidated",
            payload={"city": city},
        )

    trade_conn.execute(
        """
        INSERT INTO venue_commands (
            decision_id, intent_kind, state, updated_at, created_at
        )
        VALUES (?, 'ENTRY', 'FILLED', '2026-06-07T00:05:00+00:00', '2026-06-07T00:00:00+00:00')
        """,
        ("cmd-filled",),
    )
    trade_conn.execute(
        """
        INSERT INTO venue_commands (
            decision_id, intent_kind, state, updated_at, created_at
        )
        VALUES (?, 'ENTRY', 'ACKED', '2026-06-07T00:05:00+00:00', '2026-06-07T00:00:00+00:00')
        """,
        ("cmd-open",),
    )
    trade_conn.execute(
        """
        INSERT INTO position_current (
            phase, token_id, no_token_id, cost_basis_usd, chain_cost_basis_usd, shares
        )
        VALUES ('active', '', 'token-active-position', 4.0, 0.0, 5.0)
        """
    )

    rows = _durable_unmaterialized_live_cap_reservations(conn, trade_conn=trade_conn)

    assert rows == (
        ("durable_live_cap:usage-still-inflight", "Chicago", pytest.approx(6.0)),
    )


def test_107_durable_live_cap_seed_is_committed_and_rollback_immune():
    """Already-emitted cross-cycle live-cap exposure cannot be removed by the
    per-event rollback path, because it is real in-flight capital."""
    conn = _live_cap_seed_conn()
    _insert_live_cap_usage(
        conn,
        usage_id="usage-pending",
        event_id="event-pending",
        final_intent_id="intent-pending",
        usd=12.5,
    )
    _insert_live_order_event(
        conn,
        aggregate_id="event-pending:intent-pending",
        seq=1,
        event_type="PreSubmitRevalidated",
        payload={"city": "Chicago"},
    )

    ledger = PortfolioReservationLedger()
    seeded = _seed_portfolio_reservations_from_durable_live_cap(ledger, conn)
    assert seeded == 1
    assert list(ledger) == [("Chicago", pytest.approx(12.5))]

    ledger.rollback("durable_live_cap:usage-pending")
    assert list(ledger) == [("Chicago", pytest.approx(12.5))]


def test_107_durable_live_cap_seed_query_error_fails_closed():
    """Exposure ambiguity must not degrade to an empty seed and allow sizing."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE edli_live_cap_usage (
            usage_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            reserved_notional_usd REAL NOT NULL,
            reservation_status TEXT NOT NULL,
            final_intent_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT NOT NULL,
            event_type TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO edli_live_cap_usage (
            usage_id, event_id, reserved_notional_usd, reservation_status, final_intent_id
        )
        VALUES ('usage-bad', 'event-bad', 5.0, 'CONSUMED', 'intent-bad')
        """
    )

    with pytest.raises(RuntimeError, match="DURABLE_LIVE_CAP_EXPOSURE_SEED_UNAVAILABLE"):
        _seed_portfolio_reservations_from_durable_live_cap(
            PortfolioReservationLedger(),
            conn,
        )
