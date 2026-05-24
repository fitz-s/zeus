# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §13 reactor no direct venue adapter contract.
from __future__ import annotations

import ast
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from src.engine.discovery_mode import DiscoveryMode
from src.engine.event_reactor_adapter import (
    discovery_mode_for_event,
    edli_source_truth_gate,
    edli_trade_score_gate,
    executable_snapshot_gate_from_trade_conn,
    riskguard_allows_new_entries,
    submit_existing_cycle_for_event,
)
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.riskguard.risk_level import RiskLevel


def test_reactor_never_imports_venue_adapter():
    path = Path("src/events/reactor.py")
    tree = ast.parse(path.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert all("venue_adapter" not in imported for imported in imports)


def test_engine_adapter_builds_reactor_without_side_effect_submit():
    source = Path("src/engine/event_reactor_adapter.py").read_text()
    assert "venue_adapter" not in source
    assert "execute_final_intent" not in source


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


def test_adapter_source_truth_allows_complete_forecast_only():
    complete = _forecast_event("COMPLETE")
    partial_payload = json.loads(complete.payload_json)
    partial_payload["completeness_status"] = "PARTIAL_ALLOWED"
    partial = replace(complete, payload_json=json.dumps(partial_payload, sort_keys=True, separators=(",", ":")))

    assert edli_source_truth_gate(complete) is True
    assert edli_source_truth_gate(partial) is False


def test_adapter_trade_score_gate_uses_robust_trade_score_inputs():
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
    assert edli_trade_score_gate(negative) is False
    assert edli_trade_score_gate(event) is False


def test_adapter_maps_events_to_existing_discovery_modes():
    forecast = _forecast_event()

    assert discovery_mode_for_event(forecast) == DiscoveryMode.UPDATE_REACTION


def test_submit_existing_cycle_reports_no_submit_when_existing_path_builds_none():
    event = _forecast_event()
    seen = []

    result = submit_existing_cycle_for_event(
        event,
        run_cycle=lambda mode: seen.append(mode) or {"final_intents_built": 0, "entry_orders_submitted": 0},
    )

    assert result.submitted is False
    assert result.reason == "EXISTING_CYCLE_NO_SUBMIT"
    assert seen == [DiscoveryMode.UPDATE_REACTION]


def test_submit_existing_cycle_requires_event_bound_submit_receipt():
    event = _forecast_event()

    result = submit_existing_cycle_for_event(
        event,
        run_cycle=lambda _mode: {"final_intents_built": 1, "entry_orders_submitted": 0},
    )
    assert result.submitted is False
    assert result.reason == "UNBOUND_EXISTING_CYCLE_SUMMARY"


def test_submit_existing_cycle_accepts_event_bound_summary_only():
    event = _forecast_event()

    result = submit_existing_cycle_for_event(
        event,
        run_cycle=lambda _mode: {
            "final_intents_built": 1,
            "entry_orders_submitted": 0,
            "edli_event_id": event.event_id,
            "causal_snapshot_id": event.causal_snapshot_id,
        },
    )

    assert result.submitted is True
    assert result.event_id == event.event_id


def test_executable_snapshot_gate_requires_fresh_snapshot():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            freshness_deadline TEXT,
            snapshot_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            active INTEGER,
            closed INTEGER,
            event_slug TEXT
        )
        """
    )
    gate = executable_snapshot_gate_from_trade_conn(
        conn,
        now=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )
    event = _forecast_event()
    assert gate(event) is False
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?,?,?)",
        ("2026-05-24T12:05:00+00:00", "other-snapshot", "yes-1", "no-1", 1, 0, "chicago-weather"),
    )
    assert gate(event) is False
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?,?,?)",
        ("2026-05-24T12:05:00+00:00", event.causal_snapshot_id, "yes-1", "no-1", 1, 0, "chicago-weather"),
    )
    assert gate(event) is True


def test_riskguard_adapter_blocks_non_green():
    event = _forecast_event()
    assert riskguard_allows_new_entries(get_current_level=lambda: RiskLevel.GREEN)(event) is True
    assert riskguard_allows_new_entries(get_current_level=lambda: RiskLevel.YELLOW)(event) is False
