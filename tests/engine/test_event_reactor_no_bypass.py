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
        snapshot_id="snapshot-1",
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
def test_forecast_receipt_does_not_require_old_probability_or_selection_facts():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("DROP TABLE probability_trace_fact")
    conn.execute("DROP TABLE selection_hypothesis_fact")
    conn.execute("DROP TABLE selection_family_fact")

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

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

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=trade_conn,
        forecast_conn=forecast_conn,
        topology_conn=forecast_conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

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


def test_missing_calibration_authority_blocks_receipt():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots_v2 SET p_cal_json = NULL")

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:CALIBRATION_AUTHORITY_MISSING")


def test_receipt_revalidates_source_run_coverage_after_event_emit():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE source_run_coverage SET readiness_status = 'BLOCKED'")

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert receipt.submitted is False
    assert "FORECAST_READER_REVALIDATION_FAILED:readiness_BLOCKED" in receipt.reason


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

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert receipt.submitted is False
    assert receipt.reason.startswith("EXECUTABLE_NATIVE_ASK_MISSING")
    assert receipt.native_quote_available is False


def test_forecast_receipt_uses_attached_forecasts_market_topology():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("ALTER TABLE market_events_v2 RENAME TO attached_market_events_v2")
    conn.execute("ATTACH DATABASE ':memory:' AS forecasts")
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

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert receipt.submitted is True
    assert receipt.fdr_hypothesis_count == 4


def test_forecast_receipt_rejects_source_snapshot_available_after_event_available_time():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots_v2 SET available_at = '2026-05-24T08:11:00+00:00'")

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:causal forecast snapshot missing")


def test_forecast_receipt_requires_exact_causal_snapshot_from_source_data():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots_v2 SET snapshot_id = 'other-snapshot'")

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:causal forecast snapshot missing")


def test_forecast_receipt_requires_metric_match_in_source_snapshot():
    event = _bound_forecast_event()
    conn = _trade_conn_with_snapshot()
    conn.execute("UPDATE ensemble_snapshots_v2 SET temperature_metric = 'low'")

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert receipt.submitted is False
    assert receipt.reason.startswith("LIVE_INFERENCE_INPUTS_MISSING:causal forecast snapshot missing")


def test_day0_receipt_uses_latest_forecast_source_and_absorbing_boundary_not_old_facts():
    event = _day0_event(token_id="yes-2")
    conn = _trade_conn_with_snapshot()
    conn.execute("DROP TABLE probability_trace_fact")
    conn.execute("DROP TABLE selection_hypothesis_fact")
    conn.execute("DROP TABLE selection_family_fact")

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert receipt.submitted is True
    assert receipt.condition_id == "condition-2"
    assert receipt.token_id == "yes-2"
    assert receipt.q_live is not None
    assert receipt.q_live > 0.99
    assert receipt.fdr_hypothesis_count == 4
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_runtime_receipt_rejects_missing_native_ask_instead_of_defaulting_midpoint():
    event = _bound_forecast_event()
    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=_trade_conn_with_snapshot(selected_ask=""),
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert receipt.submitted is False
    assert receipt.reason.startswith("EXECUTABLE_NATIVE_ASK_MISSING")


def test_runtime_receipt_uses_runtime_kelly_authority_not_event_payload():
    event = _bound_forecast_event()
    payload = json.loads(event.payload_json)
    payload["bankroll_usd"] = 0
    payload["kelly_multiplier"] = 0
    event = replace(event, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))

    receipt = build_event_bound_no_submit_receipt(
        event,
        trade_conn=_trade_conn_with_snapshot(),
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert receipt.kelly_pass is True
    assert receipt.kelly_size_usd > 0
    assert receipt.reason != "KELLY_PROOF_MISSING"
