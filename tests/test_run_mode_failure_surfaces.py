# Created: 2026-05-19
# Last reused or audited: 2026-07-10
# Authority basis: codereview-may19-2.md relationship F
#                  + docs/operations/task_2026-05-21_live_side_effect_risk_boundaries/task.md P1-1
# Lifecycle: created=2026-05-19; last_reviewed=2026-07-10; last_reused=2026-07-10
# Purpose: Relationship-F antibody — assert that compute_composite_live_health()
#   surfaces DEGRADED when run_mode has failed or status_summary is stale, even
#   when the heartbeat is OK (closing the "scheduler alive but not trading" gap).
# Reuse: Run on every PR touching src/control/live_health.py, src/main.py
#   _write_heartbeat, or scheduler_jobs_health.

"""Relationship-F composite live-health antibody.

Background (codereview-may19-2.md relationship F):
> The scheduler can appear alive (heartbeat OK, process running) while
> run_mode is not successfully trading.  @_scheduler_job catches exceptions
> without re-raising (K2 fail-open design), and _run_mode() catches and writes
> a failed status_summary.  An operator watching only process PID or heartbeat
> sees "alive" while the system has degraded.

Invariant:
  live health = heartbeat OK AND latest run_mode OK
                AND status_summary fresh AND no entry blocker active

Probes:
  T1: heartbeat OK + run_mode FAILED → composite DEGRADED
  T2: all OK + status_summary stale (>5 min) → composite DEGRADED
  T3: all OK + fresh → composite HEALTHY
  T4: DEGRADED composite emits WARNING log with failing surface name
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.control import live_health
from src.control.live_health import compute_composite_live_health, STATUS_FRESH_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))


def _write_forecast_event_bridge_dbs(
    sd: Path,
    *,
    posterior_computed_at: str,
    fsr_created_at: str | None,
    posterior_identity_hash: str | None = None,
    fsr_payload: dict | None = None,
) -> None:
    forecast_conn = sqlite3.connect(sd / "zeus-forecasts.db")
    try:
        if posterior_identity_hash is None:
            forecast_conn.execute(
                "CREATE TABLE forecast_posteriors (computed_at TEXT, runtime_layer TEXT)"
            )
            forecast_conn.execute(
                "INSERT INTO forecast_posteriors (computed_at, runtime_layer) VALUES (?, 'live')",
                (posterior_computed_at,),
            )
        else:
            forecast_conn.execute(
                "CREATE TABLE forecast_posteriors ("
                "computed_at TEXT, runtime_layer TEXT, posterior_identity_hash TEXT, "
                "city TEXT, target_date TEXT, temperature_metric TEXT, "
                "source_cycle_time TEXT, source_available_at TEXT)"
            )
            forecast_conn.execute(
                "INSERT INTO forecast_posteriors ("
                "computed_at, runtime_layer, posterior_identity_hash, city, target_date, "
                "temperature_metric, source_cycle_time, source_available_at"
                ") VALUES (?, 'live', ?, ?, ?, ?, ?, ?)",
                (
                    posterior_computed_at,
                    posterior_identity_hash,
                    str((fsr_payload or {}).get("city") or "Madrid"),
                    str((fsr_payload or {}).get("target_date") or "2026-07-09"),
                    str((fsr_payload or {}).get("metric") or "high"),
                    str((fsr_payload or {}).get("cycle") or "2026-07-08T06:00:00+00:00"),
                    str((fsr_payload or {}).get("available_at") or posterior_computed_at),
                ),
            )
        forecast_conn.commit()
    finally:
        forecast_conn.close()

    world_conn = sqlite3.connect(sd / "zeus-world.db")
    try:
        if fsr_payload is None:
            world_conn.execute(
                "CREATE TABLE opportunity_events (event_id TEXT, event_type TEXT, entity_key TEXT, created_at TEXT)"
            )
        else:
            world_conn.execute(
                "CREATE TABLE opportunity_events (event_id TEXT, event_type TEXT, entity_key TEXT, created_at TEXT, payload_json TEXT)"
            )
        if fsr_created_at is not None:
            if fsr_payload is None:
                world_conn.execute(
                    "INSERT INTO opportunity_events (event_id, event_type, entity_key, created_at) "
                    "VALUES ('fsr-1', 'FORECAST_SNAPSHOT_READY', 'city|date|high', ?)",
                    (fsr_created_at,),
                )
            else:
                world_conn.execute(
                    "INSERT INTO opportunity_events (event_id, event_type, entity_key, created_at, payload_json) "
                    "VALUES ('fsr-1', 'FORECAST_SNAPSHOT_READY', 'city|date|high', ?, ?)",
                    (fsr_created_at, json.dumps(fsr_payload)),
                )
        world_conn.commit()
    finally:
        world_conn.close()


def _write_day0_trace_dbs(sd: Path, *, with_regret: bool) -> None:
    world_conn = sqlite3.connect(sd / "zeus-world.db")
    try:
        world_conn.execute(
            "CREATE TABLE opportunity_events (event_id TEXT, event_type TEXT, entity_key TEXT, created_at TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE opportunity_event_processing (consumer_name TEXT, event_id TEXT, "
            "processing_status TEXT, processed_at TEXT, last_error TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE decision_compile_failures (event_id TEXT, stage TEXT, reason_code TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE no_trade_regret_events (event_id TEXT, rejection_stage TEXT, rejection_reason TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE edli_no_submit_receipts (event_id TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE decision_certificates (certificate_type TEXT, semantic_key TEXT)"
        )
        world_conn.execute(
            "CREATE INDEX idx_decision_certificates_semantic "
            "ON decision_certificates(certificate_type, semantic_key)"
        )
        world_conn.execute(
            "INSERT INTO opportunity_events VALUES "
            "('day0-1', 'DAY0_EXTREME_UPDATED', 'Madrid|2026-07-01|high|LEMD', ?)",
            (_now_iso(-60),),
        )
        world_conn.execute(
            "INSERT INTO opportunity_event_processing VALUES "
            "('edli_reactor_v1', 'day0-1', 'processed', ?, NULL)",
            (_now_iso(-30),),
        )
        if with_regret:
            world_conn.execute(
                "INSERT INTO no_trade_regret_events VALUES "
                "('day0-1', 'TRADE_SCORE', 'EVENT_BOUND_ALL_CANDIDATES_REJECTED')"
            )
        world_conn.commit()
    finally:
        world_conn.close()

    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            "CREATE TABLE venue_commands (decision_id TEXT)"
        )
        trade_conn.execute(
            "CREATE TABLE decision_certificates (certificate_type TEXT, semantic_key TEXT)"
        )
        trade_conn.execute(
            "CREATE INDEX idx_decision_certificates_semantic "
            "ON decision_certificates(certificate_type, semantic_key)"
        )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_forecast_trace_dbs(sd: Path, *, with_no_submit: bool = False) -> None:
    world_conn = sqlite3.connect(sd / "zeus-world.db")
    try:
        world_conn.execute(
            "CREATE TABLE opportunity_events ("
            "event_id TEXT, event_type TEXT, entity_key TEXT, created_at TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE opportunity_event_processing ("
            "consumer_name TEXT, event_id TEXT, processing_status TEXT, "
            "processed_at TEXT, last_error TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE decision_compile_failures (event_id TEXT, stage TEXT, reason_code TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE no_trade_regret_events (event_id TEXT, rejection_stage TEXT, rejection_reason TEXT)"
        )
        world_conn.execute("CREATE TABLE edli_no_submit_receipts (event_id TEXT)")
        world_conn.execute(
            "CREATE TABLE decision_certificates (certificate_type TEXT, semantic_key TEXT)"
        )
        world_conn.execute(
            "CREATE INDEX idx_decision_certificates_semantic "
            "ON decision_certificates(certificate_type, semantic_key)"
        )
        world_conn.execute(
            "INSERT INTO opportunity_events VALUES "
            "('fsr-trace-1', 'FORECAST_SNAPSHOT_READY', 'Paris|2026-07-09|low', ?)",
            (_now_iso(-60),),
        )
        world_conn.execute(
            "INSERT INTO opportunity_event_processing VALUES "
            "('edli_reactor_v1', 'fsr-trace-1', 'processed', ?, NULL)",
            (_now_iso(-30),),
        )
        if with_no_submit:
            world_conn.execute(
                "INSERT INTO edli_no_submit_receipts VALUES ('fsr-trace-1')"
            )
        world_conn.commit()
    finally:
        world_conn.close()

    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute("CREATE TABLE venue_commands (decision_id TEXT)")
        trade_conn.execute(
            "CREATE TABLE decision_certificates (certificate_type TEXT, semantic_key TEXT)"
        )
        trade_conn.execute(
            "CREATE INDEX idx_decision_certificates_semantic "
            "ON decision_certificates(certificate_type, semantic_key)"
        )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_high_yes_edge_dbs(
    sd: Path,
    *,
    with_yes_no_submit: bool = False,
    with_yes_no_trade: bool = False,
    with_yes_entry_command: bool = False,
    with_stale_yes_no_trade: bool = False,
    with_degenerate_day0_lcb_no_trade: bool = False,
    stale_quote: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    condition_id = "cond-high-yes-1"
    bin_label = "Will the lowest temperature in Paris be 17C on July 9?"
    computed_at = (now - timedelta(minutes=5)).isoformat()
    forecast_conn = sqlite3.connect(sd / "zeus-forecasts.db")
    try:
        forecast_conn.execute(
            "CREATE TABLE forecast_posteriors ("
            "posterior_id INTEGER, city TEXT, target_date TEXT, "
            "temperature_metric TEXT, computed_at TEXT, runtime_layer TEXT, "
            "q_json TEXT, q_lcb_json TEXT)"
        )
        forecast_conn.execute(
            "CREATE TABLE market_events ("
            "city TEXT, target_date TEXT, temperature_metric TEXT, "
            "range_label TEXT, condition_id TEXT)"
        )
        forecast_conn.execute(
            "INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "Paris",
                "2026-07-09",
                "low",
                computed_at,
                "live",
                json.dumps({bin_label: 0.93}),
                json.dumps({bin_label: 0.91}),
            ),
        )
        forecast_conn.execute(
            "INSERT INTO market_events VALUES (?, ?, ?, ?, ?)",
            ("Paris", "2026-07-09", "low", bin_label, condition_id),
        )
        forecast_conn.commit()
    finally:
        forecast_conn.close()

    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            "CREATE TABLE executable_market_snapshot_latest ("
            "condition_id TEXT, outcome_label TEXT, orderbook_top_ask TEXT, "
            "captured_at TEXT, freshness_deadline TEXT, "
            "active INTEGER, closed INTEGER, accepting_orders INTEGER)"
        )
        trade_conn.execute(
            "CREATE TABLE venue_commands ("
            "intent_kind TEXT, created_at TEXT, side TEXT, market_id TEXT, "
            "envelope_id TEXT, decision_id TEXT)"
        )
        trade_conn.execute(
            "CREATE TABLE venue_submission_envelopes ("
            "envelope_id TEXT, condition_id TEXT, outcome_label TEXT)"
        )
        if with_yes_entry_command:
            trade_conn.execute(
                "INSERT INTO venue_submission_envelopes VALUES (?, ?, 'YES')",
                ("env-high-yes-1", condition_id),
            )
            trade_conn.execute(
                "INSERT INTO venue_commands VALUES "
                "('ENTRY', ?, 'BUY', 'market-high-yes-1', 'env-high-yes-1', ?)",
                (
                    (now - timedelta(minutes=1)).isoformat(),
                    "edli_intent:fsr-high-yes-1:buy_yes",
                ),
            )
        trade_conn.execute(
            "INSERT INTO executable_market_snapshot_latest VALUES "
            "(?, 'YES', '0.20', ?, ?, 1, 0, 1)",
            (
                condition_id,
                (now - timedelta(minutes=2)).isoformat(),
                (
                    now - timedelta(minutes=1)
                    if stale_quote
                    else now + timedelta(minutes=1)
                ).isoformat(),
            ),
        )
        trade_conn.commit()
    finally:
        trade_conn.close()

    world_conn = sqlite3.connect(sd / "zeus-world.db")
    try:
        world_conn.execute(
            "CREATE TABLE opportunity_events ("
            "event_id TEXT, event_type TEXT, payload_json TEXT, created_at TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE opportunity_event_processing ("
            "event_id TEXT, consumer_name TEXT, processing_status TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE decision_certificates ("
            "certificate_type TEXT, created_at TEXT, payload_json TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE edli_no_submit_receipts ("
            "created_at TEXT, condition_id TEXT, direction TEXT)"
        )
        world_conn.execute(
            "CREATE TABLE no_trade_regret_events ("
            "created_at TEXT, condition_id TEXT, direction TEXT, "
            "rejection_stage TEXT, rejection_reason TEXT, "
            "city TEXT, target_date TEXT, metric TEXT, bin_label TEXT, "
            "q_lcb_5pct REAL, c_fee_adjusted REAL, trade_score REAL)"
        )
        world_conn.execute(
            "INSERT INTO opportunity_events VALUES (?, 'FORECAST_SNAPSHOT_READY', ?, ?)",
            (
                "fsr-high-yes-1",
                json.dumps(
                    {
                        "city": "Paris",
                        "target_date": "2026-07-09",
                        "metric": "low",
                        "available_at": computed_at,
                    }
                ),
                (now - timedelta(minutes=4)).isoformat(),
            ),
        )
        world_conn.execute(
            "INSERT INTO opportunity_event_processing VALUES "
            "('fsr-high-yes-1', 'edli_reactor_v1', 'processed')"
        )
        if with_yes_no_submit:
            world_conn.execute(
                "INSERT INTO edli_no_submit_receipts VALUES (?, ?, ?)",
                (
                    (now - timedelta(minutes=1)).isoformat(),
                    condition_id,
                    "buy_yes",
                ),
            )
        if with_yes_no_trade:
            world_conn.execute(
                "INSERT INTO no_trade_regret_events "
                "(created_at, condition_id, direction, rejection_stage, rejection_reason, "
                "city, target_date, metric, bin_label, q_lcb_5pct, c_fee_adjusted, "
                "trade_score) "
                "VALUES (?, ?, ?, 'TRADE_SCORE', ?, 'Paris', '2026-07-09', "
                "'low', ?, 0.91, 0.20, 0.71)",
                (
                    (now - timedelta(minutes=1)).isoformat(),
                    condition_id,
                    "buy_yes",
                    "EVENT_BOUND_CANDIDATE_REJECTED:"
                    "QKERNEL_EXECUTION_ECONOMICS_FALSE_EDGE_RATE_BLOCKS:"
                    "value=0.500000:alpha=0.100000:candidate_id=abc",
                    bin_label,
                ),
            )
        if with_stale_yes_no_trade:
            world_conn.execute(
                "INSERT INTO no_trade_regret_events "
                "(created_at, condition_id, direction, rejection_stage, rejection_reason, "
                "city, target_date, metric, bin_label, q_lcb_5pct, c_fee_adjusted, "
                "trade_score) "
                "VALUES (?, ?, ?, 'TRADE_SCORE', ?, 'Paris', '2026-07-09', "
                "'low', ?, 0.91, 0.20, 0.71)",
                (
                    (now - timedelta(minutes=10)).isoformat(),
                    condition_id,
                    "buy_yes",
                    "EVENT_BOUND_CANDIDATE_REJECTED:"
                    "QKERNEL_EXECUTION_ECONOMICS_FALSE_EDGE_RATE_BLOCKS:"
                    "value=0.500000:alpha=0.100000:candidate_id=stale",
                    bin_label,
                ),
            )
        if with_degenerate_day0_lcb_no_trade:
            world_conn.execute(
                "INSERT INTO no_trade_regret_events "
                "(created_at, condition_id, direction, rejection_stage, rejection_reason, "
                "city, target_date, metric, bin_label, q_lcb_5pct, c_fee_adjusted, "
                "trade_score) "
                "VALUES (?, ?, ?, 'EXECUTOR_EXPRESSIBILITY', ?, 'Paris', '2026-07-09', "
                "'low', ?, 0.91, 0.20, 0.71)",
                (
                    (now - timedelta(minutes=1)).isoformat(),
                    condition_id,
                    "buy_yes",
                    "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:"
                    "LIVE_ENTRY_DAY0_PROBABILITY_AUTHORITY_REQUIRED:"
                    "remaining_day q_lcb is degenerate with q_live",
                    bin_label,
                ),
            )
        world_conn.commit()
    finally:
        world_conn.close()


def _write_entry_q_version_db(
    sd: Path,
    rows: list[dict[str, object]],
    *,
    include_q_version_column: bool = True,
) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        q_version_column = ", q_version TEXT" if include_q_version_column else ""
        trade_conn.execute(
            "CREATE TABLE venue_commands ("
            "command_id TEXT, position_id TEXT, intent_kind TEXT, state TEXT, created_at TEXT"
            f"{q_version_column})"
        )
        for row in rows:
            if include_q_version_column:
                trade_conn.execute(
                    "INSERT INTO venue_commands "
                    "(command_id, position_id, intent_kind, state, created_at, q_version) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row.get("command_id"),
                        row.get("position_id"),
                        row.get("intent_kind", "ENTRY"),
                        row.get("state", "ACKED"),
                        row.get("created_at", _now_iso(-30)),
                        row.get("q_version"),
                    ),
                )
            else:
                trade_conn.execute(
                    "INSERT INTO venue_commands "
                    "(command_id, position_id, intent_kind, state, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        row.get("command_id"),
                        row.get("position_id"),
                        row.get("intent_kind", "ENTRY"),
                        row.get("state", "ACKED"),
                        row.get("created_at", _now_iso(-30)),
                    ),
                )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_position_current_rows(sd: Path, rows: list[dict[str, object]]) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            "CREATE TABLE position_current ("
            "position_id TEXT PRIMARY KEY, phase TEXT, order_status TEXT, "
            "shares REAL, chain_shares REAL)"
        )
        for row in rows:
            trade_conn.execute(
                "INSERT INTO position_current "
                "(position_id, phase, order_status, shares, chain_shares) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    row.get("position_id"),
                    row.get("phase", "active"),
                    row.get("order_status", "filled"),
                    row.get("shares", 0.0),
                    row.get("chain_shares", row.get("shares", 0.0)),
                ),
            )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _attach_entry_snapshot_id(sd: Path, *, command_id: str, snapshot_id: str) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute("ALTER TABLE venue_commands ADD COLUMN snapshot_id TEXT")
        trade_conn.execute(
            "UPDATE venue_commands SET snapshot_id = ? WHERE command_id = ?",
            (snapshot_id, command_id),
        )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _attach_entry_decision_id(sd: Path, *, command_id: str, decision_id: str) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute("ALTER TABLE venue_commands ADD COLUMN decision_id TEXT")
        trade_conn.execute(
            "UPDATE venue_commands SET decision_id = ? WHERE command_id = ?",
            (decision_id, command_id),
        )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_final_intent_certificate(
    sd: Path,
    *,
    snapshot_id: str,
    posterior_identity_hash: str,
    q_live: float,
    q_lcb_5pct: float,
) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            """
            CREATE TABLE decision_certificates (
                certificate_id TEXT,
                certificate_type TEXT,
                decision_time TEXT,
                payload_json TEXT,
                certificate_hash TEXT,
                created_at TEXT
            )
            """
        )
        payload = {
            "executable_snapshot_id": snapshot_id,
            "q_live": q_live,
            "q_lcb_5pct": q_lcb_5pct,
            "selection_authority_applied": "qkernel_spine",
            "decision_source_context": {
                "snapshot_id": snapshot_id,
                "posterior_identity_hash": posterior_identity_hash,
                "forecast_source_id": "openmeteo_ecmwf_ifs9_bayes_fusion_high_v1",
                "source_available_at": "2026-07-08T12:31:30+00:00",
                "forecast_available_at": "2026-07-08T12:31:30+00:00",
            },
        }
        trade_conn.execute(
            """
            INSERT INTO decision_certificates (
                certificate_id,
                certificate_type,
                decision_time,
                payload_json,
                certificate_hash,
                created_at
            ) VALUES (
                'FinalIntentCertificate:test',
                'FinalIntentCertificate',
                '2026-07-08T15:00:00+00:00',
                ?,
                ?,
                '2026-07-08T15:00:05+00:00'
            )
            """,
            (json.dumps(payload, sort_keys=True), "f" * 64),
        )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_execution_to_final_intent_edge(
    sd: Path,
    *,
    decision_id: str,
    final_executable_snapshot_id: str,
    posterior_identity_hash: str,
    q_live: float,
    q_lcb_5pct: float,
) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            """
            CREATE TABLE decision_certificates (
                certificate_id TEXT,
                certificate_type TEXT,
                semantic_key TEXT,
                decision_time TEXT,
                payload_json TEXT,
                certificate_hash TEXT,
                created_at TEXT
            )
            """
        )
        trade_conn.execute(
            """
            CREATE TABLE decision_certificate_edges (
                child_certificate_id TEXT,
                parent_role TEXT,
                parent_certificate_hash TEXT,
                parent_certificate_type TEXT,
                required INTEGER,
                created_at TEXT
            )
            """
        )
        final_hash = "e" * 64
        final_payload = {
            "executable_snapshot_id": final_executable_snapshot_id,
            "q_live": q_live,
            "q_lcb_5pct": q_lcb_5pct,
            "selection_authority_applied": "qkernel_spine",
            "decision_source_context": {
                "snapshot_id": "rmf-edge-chain-snapshot",
                "posterior_identity_hash": posterior_identity_hash,
                "forecast_source_id": "openmeteo_ecmwf_ifs9_bayes_fusion_high_v1",
            },
        }
        trade_conn.execute(
            """
            INSERT INTO decision_certificates VALUES (
                'ExecutionCommandCertificate:test',
                'ExecutionCommandCertificate',
                ?,
                '2026-07-08T15:00:01+00:00',
                ?,
                ?,
                '2026-07-08T15:00:03+00:00'
            )
            """,
            (
                f"execution_command:event:{decision_id}",
                json.dumps({"execution_command_id": decision_id}, sort_keys=True),
                "d" * 64,
            ),
        )
        trade_conn.execute(
            """
            INSERT INTO decision_certificates VALUES (
                'FinalIntentCertificate:edge',
                'FinalIntentCertificate',
                'final_intent:event:intent',
                '2026-07-08T15:00:00+00:00',
                ?,
                ?,
                '2026-07-08T15:00:02+00:00'
            )
            """,
            (json.dumps(final_payload, sort_keys=True), final_hash),
        )
        trade_conn.execute(
            """
            INSERT INTO decision_certificate_edges VALUES (
                'ExecutionCommandCertificate:test',
                'final_intent',
                ?,
                'FinalIntentCertificate',
                1,
                '2026-07-08T15:00:04+00:00'
            )
            """,
            (final_hash,),
        )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_pending_exit_release_loop_db(sd: Path, *, now: datetime) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            "CREATE TABLE IF NOT EXISTS venue_commands ("
            "command_id TEXT, position_id TEXT, intent_kind TEXT, state TEXT, "
            "created_at TEXT, q_version TEXT)"
        )
        trade_conn.execute(
            "INSERT INTO venue_commands VALUES "
            "('cmd-with-q', 'pos-loop', 'ENTRY', 'CANCELLED', ?, 'q-id-1')",
            ((now - timedelta(minutes=3)).isoformat(),),
        )
        trade_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                order_status TEXT,
                shares REAL,
                chain_shares REAL,
                city TEXT,
                target_date TEXT,
                bin_label TEXT,
                direction TEXT,
                exit_reason TEXT
            )
            """
        )
        trade_conn.execute(
            """
            INSERT INTO position_current VALUES (
                'pos-loop',
                'pending_exit',
                'retry_pending',
                1.0,
                1.0,
                'Kuala Lumpur',
                '2026-07-08',
                'Will the highest temperature in Kuala Lumpur be 33°C on July 8?',
                'buy_no',
                'DAY0_HARD_FACT_BIN_DEAD'
            )
            """
        )
        trade_conn.execute(
            """
            CREATE TABLE position_events (
                position_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                occurred_at TEXT,
                phase_before TEXT,
                phase_after TEXT,
                venue_status TEXT,
                payload_json TEXT
            )
            """
        )
        events = [
            (
                10,
                "EXIT_RETRY_RELEASED",
                now - timedelta(minutes=9),
                "pending_exit",
                "day0_window",
                "ready",
            ),
            (
                11,
                "EXIT_INTENT",
                now - timedelta(minutes=8, seconds=50),
                "day0_window",
                "pending_exit",
                "exit_intent",
            ),
            (
                20,
                "EXIT_RETRY_RELEASED",
                now - timedelta(minutes=3),
                "pending_exit",
                "day0_window",
                "ready",
            ),
            (
                21,
                "EXIT_INTENT",
                now - timedelta(minutes=2, seconds=50),
                "day0_window",
                "pending_exit",
                "exit_intent",
            ),
        ]
        for seq, event_type, occurred_at, before, after, status in events:
            trade_conn.execute(
                "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pos-loop",
                    seq,
                    event_type,
                    occurred_at.isoformat(),
                    before,
                    after,
                    status,
                    json.dumps({"exit_reason": "DAY0_HARD_FACT_BIN_DEAD"}),
                ),
            )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_pending_exit_no_exit_command_db(sd: Path, *, now: datetime) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            "CREATE TABLE IF NOT EXISTS venue_commands ("
            "command_id TEXT, position_id TEXT, intent_kind TEXT, state TEXT, "
            "created_at TEXT, q_version TEXT)"
        )
        trade_conn.execute(
            "INSERT INTO venue_commands VALUES "
            "('cmd-entry-only', 'pos-no-exit-command', 'ENTRY', 'CANCELLED', ?, 'q-id-1')",
            ((now - timedelta(hours=2)).isoformat(),),
        )
        trade_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                order_status TEXT,
                shares REAL,
                chain_shares REAL,
                city TEXT,
                target_date TEXT,
                bin_label TEXT,
                direction TEXT,
                exit_reason TEXT,
                updated_at TEXT
            )
            """
        )
        trade_conn.execute(
            """
            INSERT INTO position_current VALUES (
                'pos-no-exit-command',
                'pending_exit',
                'exit_intent',
                19.98,
                19.98,
                'Shenzhen',
                '2026-07-09',
                'Will the highest temperature in Shenzhen be 32°C on July 9?',
                'buy_no',
                'FAMILY_DIRECT_SELL_DOMINATES_HOLD',
                ?
            )
            """,
            ((now - timedelta(minutes=5)).isoformat(),),
        )
        trade_conn.execute(
            """
            CREATE TABLE position_events (
                position_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                occurred_at TEXT,
                phase_before TEXT,
                phase_after TEXT,
                venue_status TEXT,
                payload_json TEXT
            )
            """
        )
        trade_conn.execute(
            "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "pos-no-exit-command",
                10,
                "EXIT_INTENT",
                (now - timedelta(minutes=5)).isoformat(),
                "quarantined",
                "pending_exit",
                "exit_intent",
                json.dumps({"exit_reason": "FAMILY_DIRECT_SELL_DOMINATES_HOLD"}),
            ),
        )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_pending_exit_reassert_loop_db(sd: Path, *, now: datetime) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            "CREATE TABLE venue_commands ("
            "command_id TEXT, position_id TEXT, intent_kind TEXT, state TEXT, "
            "created_at TEXT, q_version TEXT)"
        )
        trade_conn.execute(
            "INSERT INTO venue_commands VALUES "
            "('cmd-with-q', 'pos-reassert', 'ENTRY', 'CANCELLED', ?, 'q-id-1')",
            ((now - timedelta(minutes=4)).isoformat(),),
        )
        trade_conn.execute(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                order_status TEXT,
                shares REAL,
                chain_shares REAL,
                city TEXT,
                target_date TEXT,
                bin_label TEXT,
                direction TEXT,
                exit_reason TEXT
            )
            """
        )
        trade_conn.execute(
            """
            INSERT INTO position_current VALUES (
                'pos-reassert',
                'day0_window',
                'filled',
                1.0,
                1.0,
                'Kuala Lumpur',
                '2026-07-08',
                'Will the highest temperature in Kuala Lumpur be 33°C on July 8?',
                'buy_no',
                'MARKET_CLOSED_AWAITING_SETTLEMENT'
            )
            """
        )
        trade_conn.execute(
            """
            CREATE TABLE position_events (
                position_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                occurred_at TEXT,
                phase_before TEXT,
                phase_after TEXT,
                venue_status TEXT,
                payload_json TEXT
            )
            """
        )
        events = [
            (10, "EXIT_INTENT", now - timedelta(minutes=8), "day0_window", "pending_exit", "exit_intent"),
            (11, "EXIT_ORDER_REJECTED", now - timedelta(minutes=8), "day0_window", "pending_exit", "retry_pending"),
            (12, "MONITOR_REFRESHED", now - timedelta(minutes=8), "day0_window", "day0_window", "filled"),
            (20, "EXIT_INTENT", now - timedelta(minutes=5), "day0_window", "pending_exit", "exit_intent"),
            (21, "EXIT_ORDER_REJECTED", now - timedelta(minutes=5), "day0_window", "pending_exit", "retry_pending"),
            (22, "MONITOR_REFRESHED", now - timedelta(minutes=5), "day0_window", "day0_window", "filled"),
            (30, "EXIT_INTENT", now - timedelta(minutes=2), "day0_window", "pending_exit", "exit_intent"),
            (31, "EXIT_ORDER_REJECTED", now - timedelta(minutes=2), "day0_window", "pending_exit", "retry_pending"),
            (32, "MONITOR_REFRESHED", now - timedelta(minutes=1), "day0_window", "day0_window", "filled"),
        ]
        for seq, event_type, occurred_at, before, after, status in events:
            trade_conn.execute(
                "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pos-reassert",
                    seq,
                    event_type,
                    occurred_at.isoformat(),
                    before,
                    after,
                    status,
                    json.dumps({"exit_reason": "DAY0_HARD_FACT_BIN_DEAD"}),
                ),
            )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_pending_exit_projection_regression_db(
    sd: Path,
    *,
    now: datetime,
    released: bool = False,
) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            "CREATE TABLE IF NOT EXISTS venue_commands ("
            "command_id TEXT, position_id TEXT, intent_kind TEXT, state TEXT, "
            "created_at TEXT, q_version TEXT)"
        )
        trade_conn.execute(
            "INSERT INTO venue_commands VALUES "
            "('cmd-entry-only', 'pos-regression', 'ENTRY', 'FILLED', ?, 'q-id-1')",
            ((now - timedelta(minutes=10)).isoformat(),),
        )
        trade_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                order_status TEXT,
                shares REAL,
                chain_shares REAL,
                city TEXT,
                target_date TEXT,
                bin_label TEXT,
                direction TEXT,
                exit_reason TEXT
            )
            """
        )
        trade_conn.execute(
            """
            INSERT INTO position_current (
                position_id,
                phase,
                order_status,
                shares,
                chain_shares,
                city,
                target_date,
                bin_label,
                direction,
                exit_reason
            ) VALUES (
                'pos-regression',
                'day0_window',
                'partial',
                3.8,
                3.8,
                'Taipei',
                '2026-07-09',
                'Will the highest temperature in Taipei be 35°C on July 9?',
                'buy_no',
                'FAMILY_DIRECT_SELL_DOMINATES_HOLD [DUST: min_order_size=5]'
            )
            """
        )
        trade_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS position_events (
                position_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                occurred_at TEXT,
                phase_before TEXT,
                phase_after TEXT,
                venue_status TEXT,
                payload_json TEXT
            )
            """
        )
        events = [
            (
                10,
                "EXIT_INTENT",
                now - timedelta(minutes=6),
                "day0_window",
                "pending_exit",
                "exit_intent",
                {"exit_reason": "FAMILY_DIRECT_SELL_DOMINATES_HOLD"},
            ),
            (
                11,
                "EXIT_ORDER_REJECTED",
                now - timedelta(minutes=5, seconds=55),
                "day0_window",
                "pending_exit",
                "backoff_exhausted",
                {
                    "error": "executable_snapshot_gate: size below min_order_size",
                    "exit_reason": "FAMILY_DIRECT_SELL_DOMINATES_HOLD [DUST]",
                    "status": "backoff_exhausted",
                },
            ),
        ]
        if released:
            events.append(
                (
                    12,
                    "EXIT_RETRY_RELEASED",
                    now - timedelta(minutes=5, seconds=52),
                    "pending_exit",
                    "day0_window",
                    "ready",
                    {
                        "error": "executable_snapshot_gate: size below min_order_size",
                        "status": "ready",
                    },
                )
            )
        next_sequence = 13 if released else 12
        events.extend([
            (
                next_sequence,
                "MONITOR_REFRESHED",
                now - timedelta(minutes=5, seconds=50),
                "day0_window",
                "day0_window",
                "partial",
                {"fresh_prob": 0.77},
            ),
            (
                next_sequence + 1,
                "CHAIN_SIZE_CORRECTED",
                now - timedelta(minutes=5, seconds=40),
                "day0_window",
                "day0_window",
                "partial",
                {"chain_shares": 3.8},
            ),
        ])
        for seq, event_type, occurred_at, before, after, status, payload in events:
            trade_conn.execute(
                """
                INSERT INTO position_events (
                    position_id,
                    sequence_no,
                    event_type,
                    occurred_at,
                    phase_before,
                    phase_after,
                    venue_status,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "pos-regression",
                    seq,
                    event_type,
                    occurred_at.isoformat(),
                    before,
                    after,
                    status,
                    json.dumps(payload),
                ),
            )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_pending_exit_runtime_gate_block_db(sd: Path, *, now: datetime) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                order_status TEXT,
                shares REAL,
                chain_shares REAL,
                city TEXT,
                target_date TEXT,
                bin_label TEXT,
                direction TEXT,
                exit_reason TEXT
            )
            """
        )
        trade_conn.execute(
            """
            INSERT INTO position_current (
                position_id,
                phase,
                order_status,
                shares,
                chain_shares,
                city,
                target_date,
                bin_label,
                direction,
                exit_reason
            ) VALUES (
                'pos-runtime-gate',
                'day0_window',
                'partial',
                11.6,
                11.6,
                'Taipei',
                '2026-07-09',
                'Will the highest temperature in Taipei be 36°C on July 9?',
                'buy_no',
                'FAMILY_DIRECT_SELL_DOMINATES_HOLD'
            )
            """
        )
        trade_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS position_events (
                position_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                occurred_at TEXT,
                phase_before TEXT,
                phase_after TEXT,
                venue_status TEXT,
                payload_json TEXT
            )
            """
        )
        for seq in (10, 20):
            trade_conn.execute(
                "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pos-runtime-gate",
                    seq,
                    "EXIT_ORDER_REJECTED",
                    (now - timedelta(minutes=3 - (seq // 10))).isoformat(),
                    "pending_exit",
                    "pending_exit",
                    "retry_pending",
                    json.dumps(
                        {
                            "status": "retry_pending",
                            "runtime_submit_gate_block": True,
                            "exit_reason": "FAMILY_DIRECT_SELL_DOMINATES_HOLD",
                            "error": "structured_runtime_gate_block_without_legacy_text",
                        }
                    ),
                ),
            )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_pending_exit_historical_churn_db(sd: Path, *, now: datetime) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            "CREATE TABLE venue_commands ("
            "command_id TEXT, position_id TEXT, intent_kind TEXT, state TEXT, "
            "created_at TEXT, q_version TEXT)"
        )
        trade_conn.execute(
            "INSERT INTO venue_commands VALUES "
            "('cmd-with-q', 'pos-churn', 'ENTRY', 'CANCELLED', ?, 'q-id-1')",
            ((now - timedelta(hours=3)).isoformat(),),
        )
        trade_conn.execute(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                order_status TEXT,
                shares REAL,
                chain_shares REAL,
                city TEXT,
                target_date TEXT,
                bin_label TEXT,
                direction TEXT,
                exit_reason TEXT
            )
            """
        )
        trade_conn.execute(
            """
            INSERT INTO position_current VALUES (
                'pos-churn',
                'day0_window',
                'partial',
                1.0,
                1.0,
                'Kuala Lumpur',
                '2026-07-08',
                'Will the highest temperature in Kuala Lumpur be 33°C on July 8?',
                'buy_no',
                'DAY0_HARD_FACT_BIN_DEAD_MARKET_CLOSED'
            )
            """
        )
        trade_conn.execute(
            """
            CREATE TABLE position_events (
                position_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                occurred_at TEXT,
                phase_before TEXT,
                phase_after TEXT,
                venue_status TEXT,
                payload_json TEXT
            )
            """
        )
        seq = 1
        for idx in range(12):
            occurred_at = now - timedelta(hours=2, minutes=idx * 4)
            trade_conn.execute(
                "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pos-churn",
                    seq,
                    "EXIT_INTENT",
                    occurred_at.isoformat(),
                    "day0_window",
                    "pending_exit",
                    "exit_intent",
                    json.dumps({"exit_reason": "DAY0_HARD_FACT_BIN_DEAD"}),
                ),
            )
            seq += 1
            if idx < 6:
                trade_conn.execute(
                    "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "pos-churn",
                        seq,
                        "EXIT_ORDER_REJECTED",
                        (occurred_at + timedelta(seconds=10)).isoformat(),
                        "day0_window",
                        "pending_exit",
                        "retry_pending",
                        json.dumps({"reason": "closed_market_no_price"}),
                    ),
                )
                seq += 1
            if idx < 4:
                trade_conn.execute(
                    "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "pos-churn",
                        seq,
                        "EXIT_RETRY_RELEASED",
                        (occurred_at + timedelta(seconds=20)).isoformat(),
                        "pending_exit",
                        "day0_window",
                        "partial",
                        json.dumps({"release_reason": "PENDING_EXIT_NO_ORDER_RELEASED"}),
                    ),
                )
                seq += 1
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_pending_exit_current_churn_db(sd: Path, *, now: datetime) -> None:
    _write_pending_exit_historical_churn_db(sd, now=now)
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        latest_seq = trade_conn.execute(
            "SELECT MAX(sequence_no) FROM position_events WHERE position_id = 'pos-churn'"
        ).fetchone()[0]
        trade_conn.execute(
            "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "pos-churn",
                int(latest_seq or 0) + 1,
                "EXIT_INTENT",
                (now - timedelta(minutes=5)).isoformat(),
                "day0_window",
                "pending_exit",
                "exit_intent",
                json.dumps({"exit_reason": "DAY0_HARD_FACT_BIN_DEAD"}),
            ),
        )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_monitor_probability_freshness_db(
    sd: Path,
    *,
    now: datetime,
    latest_event_fresh: bool,
    projection_fresh: bool = True,
    stale_event_age: timedelta = timedelta(minutes=3),
    latest_event_age: timedelta = timedelta(minutes=1),
    day0_daily_extrema_receipt: str | None = None,
) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            "CREATE TABLE venue_commands ("
            "command_id TEXT, position_id TEXT, intent_kind TEXT, state TEXT, "
            "created_at TEXT, q_version TEXT)"
        )
        trade_conn.execute(
            "INSERT INTO venue_commands VALUES "
            "('cmd-with-q', 'pos-monitor', 'ENTRY', 'FILLED', ?, 'q-id-1')",
            ((now - timedelta(minutes=4)).isoformat(),),
        )
        trade_conn.execute(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                order_status TEXT,
                shares REAL,
                chain_shares REAL,
                last_monitor_prob REAL,
                last_monitor_prob_is_fresh INTEGER,
                updated_at TEXT,
                city TEXT,
                target_date TEXT,
                bin_label TEXT,
                direction TEXT
            )
            """
        )
        trade_conn.execute(
            """
            INSERT INTO position_current VALUES (
                'pos-monitor',
                'day0_window',
                'partial',
                2.0,
                2.0,
                0.42,
                ?,
                ?,
                'Kuala Lumpur',
                '2026-07-08',
                'Will the highest temperature in Kuala Lumpur be 33°C on July 8?',
                'buy_no'
            )
            """,
            (1 if projection_fresh else 0, (now - timedelta(seconds=30)).isoformat()),
        )
        trade_conn.execute(
            """
            CREATE TABLE position_events (
                position_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                occurred_at TEXT,
                payload_json TEXT
            )
            """
        )
        stale_payload = json.dumps(
            {
                "last_monitor_prob": 0.41,
                "last_monitor_prob_is_fresh": False,
            }
        )
        latest_payload_obj = {
            "last_monitor_prob": 0.42,
            "last_monitor_prob_is_fresh": latest_event_fresh,
        }
        if day0_daily_extrema_receipt == "unconditioned":
            latest_payload_obj["day0_monitor_probability_receipt"] = {
                "selected_method": "day0_observation_remaining_window",
                "remaining_window": {
                    "source": "day0_raw_model_extrema",
                    "forecast_source_validations": [
                        "forecast_source_id:raw_model_forecasts.single_runs",
                        "forecast_source_role:day0_daily_extrema_live",
                        "forecast_source_cycle_time:2026-07-09T02:14:47+00:00",
                    ],
                },
            }
        elif day0_daily_extrema_receipt == "conditioned":
            latest_payload_obj["day0_monitor_probability_receipt"] = {
                "selected_method": "day0_observation_conditioned_daily_extrema",
                "remaining_window": {
                    "source": "day0_observed_bound_conditioned_daily_extrema",
                    "forecast_source_validations": [
                        "forecast_source_id:raw_model_forecasts.single_runs",
                        "forecast_source_role:day0_daily_extrema_live",
                        "forecast_source_cycle_time:2026-07-09T02:14:47+00:00",
                        "day0_daily_extrema_conditioned_on_observed_bound",
                    ],
                },
            }
        latest_payload = json.dumps(latest_payload_obj)
        trade_conn.execute(
            "INSERT INTO position_events VALUES (?, ?, ?, ?, ?)",
            (
                "pos-monitor",
                10,
                "MONITOR_REFRESHED",
                (now - stale_event_age).isoformat(),
                stale_payload,
            ),
        )
        trade_conn.execute(
            "INSERT INTO position_events VALUES (?, ?, ?, ?, ?)",
            (
                "pos-monitor",
                11,
                "MONITOR_REFRESHED",
                (now - latest_event_age).isoformat(),
                latest_payload,
            ),
        )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _write_sub_min_partial_position_db(
    sd: Path,
    *,
    now: datetime,
    chain_shares: float,
    min_order_size: str = "5",
) -> None:
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        trade_conn.execute(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                order_status TEXT,
                shares REAL,
                chain_shares REAL,
                token_id TEXT,
                condition_id TEXT,
                city TEXT,
                target_date TEXT,
                bin_label TEXT,
                direction TEXT,
                exit_reason TEXT,
                updated_at TEXT
            )
            """
        )
        trade_conn.execute(
            """
            INSERT INTO position_current VALUES (
                'pos-sub-min',
                'day0_window',
                'partial',
                ?,
                ?,
                'token-no-sub-min',
                'cond-sub-min',
                'Taipei',
                '2026-07-09',
                'Will the highest temperature in Taipei be 35°C on July 9?',
                'buy_no',
                NULL,
                ?
            )
            """,
            (chain_shares, chain_shares, (now - timedelta(seconds=30)).isoformat()),
        )
        trade_conn.execute(
            """
            CREATE TABLE executable_market_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                condition_id TEXT,
                selected_outcome_token_id TEXT,
                min_order_size TEXT,
                orderbook_top_bid TEXT,
                orderbook_top_ask TEXT,
                captured_at TEXT,
                freshness_deadline TEXT
            )
            """
        )
        trade_conn.execute(
            """
            INSERT INTO executable_market_snapshots VALUES (
                'snap-sub-min',
                'cond-sub-min',
                'token-no-sub-min',
                ?,
                '0.999',
                'ABSENT',
                ?,
                ?
            )
            """,
            (
                min_order_size,
                (now - timedelta(seconds=10)).isoformat(),
                (now + timedelta(minutes=1)).isoformat(),
            ),
        )
        trade_conn.commit()
    finally:
        trade_conn.close()


def _healthy_execution_capability() -> dict:
    return {
        "entry": {
            "status": "allowed",
            "global_allow_submit": True,
            "components": [
                {"component": "heartbeat_supervisor", "allowed": True, "reason": "allowed"},
                {"component": "risk_allocator_global", "allowed": True, "reason": "ok"},
            ],
            "unavailable_components": [],
        },
        "exit": {
            "status": "allowed",
            "global_allow_submit": True,
            "components": [
                {"component": "heartbeat_supervisor", "allowed": True, "reason": "allowed"},
                {"component": "risk_allocator_global", "allowed": True, "reason": "ok"},
            ],
            "unavailable_components": [],
        },
    }


def _setup_healthy_state(sd: Path, offset_seconds: int = -30) -> None:
    """Write all composite surfaces in a healthy / fresh state."""
    cycle_time = _now_iso(offset_seconds)
    current_head = live_health._current_git_head()
    assert current_head, "test requires a git checkout with HEAD available"
    _write(sd / "loaded_sha.json", {"loaded_sha": current_head, "generated_at": cycle_time})
    _write(
        sd / "daemon-heartbeat.json",
        {"alive": True, "timestamp": cycle_time, "mode": "live"},
    )
    _write(
        sd / "venue-heartbeat-keeper.json",
        {
            "health": "HEALTHY",
            "resting_order_safe": True,
            "written_at": _now_iso(-5),
            "cadence_seconds": 5,
        },
    )
    for _, filename, _max_age_seconds in live_health.LIVE_BOOT_SIDECAR_HEARTBEATS:
        _write(
            sd / filename,
            {
                "git_head": current_head[:8],
                "written_at": cycle_time,
            },
        )
    _write(
        sd / "scheduler_jobs_health.json",
        {"_run_mode": {"status": "OK", "last_run_at": cycle_time, "last_success_at": cycle_time}},
    )
    _write(
        sd / "status_summary.json",
        {
            "timestamp": cycle_time,
            "cycle": {
                "mode": "opening_hunt",
                "started_at": cycle_time,
                "completed_at": cycle_time,
                "candidates": 1,
                "final_intents_built": 1,
                "entry_orders_submitted": 1,
                "venue_acks": 1,
                "trades": 0,
                "exits": 0,
                "no_trades": 0,
                "top_no_trade_reasons": {},
                "command_recovery": {"scanned": 0, "advanced": 0},
                "chain_sync": {"synced": 0},
            },
            "execution_capability": _healthy_execution_capability(),
        },
    )


# ---------------------------------------------------------------------------
# T1: heartbeat OK + run_mode FAILED → DEGRADED
# ---------------------------------------------------------------------------

def test_run_mode_failed_yields_degraded(tmp_path: Path) -> None:
    """T1: run_mode FAILED makes composite DEGRADED even with healthy heartbeat."""
    sd = tmp_path / "state"
    sd.mkdir()

    _write(
        sd / "daemon-heartbeat.json",
        {"alive": True, "timestamp": _now_iso(-30), "mode": "live"},
    )
    _write(
        sd / "scheduler_jobs_health.json",
        {
            "_run_mode": {
                "status": "FAILED",
                "last_run_at": _now_iso(-30),
                "last_failure_reason": "ValueError: no open markets",
            }
        },
    )
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 0,
                "entry_orders_submitted": 0,
                "trades": 0,
                "exits": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["healthy"] is False, f"run_mode FAILED must yield DEGRADED: {result}"
    assert result["status"] == "DEGRADED"
    assert "run_mode" in result["failing_surfaces"]
    assert result["surfaces"]["run_mode"]["ok"] is False
    assert "RUN_MODE_FAILED" in (result["surfaces"]["run_mode"]["issue"] or "")
    # heartbeat must still show OK
    assert result["surfaces"]["heartbeat"]["ok"] is True


def test_mode_specific_run_mode_failed_yields_degraded_in_legacy_cron(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_mode catches exceptions, so mode-specific failure is the authority."""
    monkeypatch.setattr(live_health, "_live_execution_mode", lambda: "legacy_cron")
    sd = tmp_path / "state"
    sd.mkdir()
    cycle_time = _now_iso(-30)
    _write(
        sd / "daemon-heartbeat.json",
        {"alive": True, "timestamp": cycle_time, "mode": "live"},
    )
    _write(
        sd / "scheduler_jobs_health.json",
        {
            "run_mode": {"status": "OK", "last_run_at": cycle_time, "last_success_at": cycle_time},
            "run_mode:opening_hunt": {
                "status": "FAILED",
                "last_run_at": cycle_time,
                "last_failure_reason": "exchange reconcile stuck",
            },
        },
    )
    _write(
        sd / "status_summary.json",
        {
            "timestamp": cycle_time,
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": cycle_time,
                "candidates": 0,
                "entry_orders_submitted": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["run_mode"]["ok"] is False
    assert result["surfaces"]["run_mode"]["issue"] == (
        "RUN_MODE_FAILED[run_mode:opening_hunt]: exchange reconcile stuck"
    )


def test_legacy_run_mode_failure_ignored_in_edli_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """edli_live does not register legacy cron run_mode jobs; stale rows are not live evidence."""
    monkeypatch.setattr(live_health, "_live_execution_mode", lambda: "edli_live")
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    scheduler = json.loads((sd / "scheduler_jobs_health.json").read_text())
    scheduler["run_mode:day0_capture"] = {
        "status": "FAILED",
        "last_run_at": "2026-06-14T00:47:10+00:00",
        "last_failure_reason": "legacy cron stale row",
    }
    _write(sd / "scheduler_jobs_health.json", scheduler)

    result = compute_composite_live_health(state_dir=sd)

    assert result["surfaces"]["run_mode"]["ok"] is True
    assert "run_mode" not in result["failing_surfaces"]


def test_forecast_event_bridge_not_evaluated_without_attested_main_daemon(
    tmp_path: Path,
) -> None:
    """A stopped main daemon should not turn posterior-vs-FSR lag into a false blocker."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)

    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(minutes=20)).isoformat(),
        fsr_created_at=(now - timedelta(hours=2)).isoformat(),
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    bridge = result["surfaces"]["forecast_event_bridge"]
    assert bridge["ok"] is True
    assert bridge["issue"] == "NOT_EVALUATED_MAIN_DAEMON_NOT_ATTESTED"
    assert "forecast_event_bridge" not in result["failing_surfaces"]


def test_forecast_event_bridge_degrades_when_live_posterior_does_not_emit_fsr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When src.main is attested, fresh posterior production must reach FSR emission."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(minutes=20)).isoformat(),
        fsr_created_at=(now - timedelta(hours=2)).isoformat(),
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    bridge = result["surfaces"]["forecast_event_bridge"]
    assert bridge["ok"] is False
    assert "FORECAST_TO_EVENT_BRIDGE_STALLED" in bridge["issue"]
    assert "forecast_event_bridge" in result["failing_surfaces"]


def test_forecast_event_bridge_reports_active_queue_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bridge stall should expose whether active queue debt is contributing."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(minutes=20)).isoformat(),
        fsr_created_at=(now - timedelta(hours=2)).isoformat(),
    )
    world_conn = sqlite3.connect(sd / "zeus-world.db")
    try:
        world_conn.execute(
            "CREATE TABLE opportunity_event_processing ("
            "consumer_name TEXT, event_id TEXT, processing_status TEXT, "
            "last_error TEXT, updated_at TEXT)"
        )
        world_conn.execute(
            "INSERT INTO opportunity_event_processing VALUES "
            "('edli_reactor_v1', 'fsr-1', 'pending', '', ?)",
            ((now - timedelta(minutes=15)).isoformat(),),
        )
        world_conn.execute(
            "INSERT INTO opportunity_events "
            "(event_id, event_type, entity_key, created_at) VALUES "
            "('fsr-terminal', 'FORECAST_SNAPSHOT_READY', 'city|date|high|old', ?)",
            ((now - timedelta(hours=3)).isoformat(),),
        )
        world_conn.execute(
            "INSERT INTO opportunity_event_processing VALUES "
            "('edli_reactor_v1', 'fsr-terminal', 'pending', "
            "'QKERNEL_ACTUAL_SUBMIT_QUALITY_FLOOR:actual_profit_below_strategy_floor', ?)",
            ((now - timedelta(minutes=14)).isoformat(),),
        )
        world_conn.commit()
    finally:
        world_conn.close()

    result = compute_composite_live_health(state_dir=sd, now=now)

    bridge = result["surfaces"]["forecast_event_bridge"]
    assert bridge["ok"] is False
    queue = bridge["event_queue"]
    assert queue["evaluated"] is True
    assert queue["active_fsr_count"] == 2
    assert queue["active_fsr_blank_error_count"] == 1
    assert queue["terminal_quality_retry_debt_count"] == 1
    assert queue["cause_hints"] == ["terminal_quality_retry_debt", "active_fsr_backlog"]


def test_forecast_event_bridge_degrades_when_live_posterior_stale_even_with_newer_fsr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer FSR row cannot mask stale live posterior production."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(minutes=20)).isoformat(),
        fsr_created_at=(now - timedelta(minutes=1)).isoformat(),
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    bridge = result["surfaces"]["forecast_event_bridge"]
    assert bridge["ok"] is False
    assert bridge["issue"].startswith("LIVE_POSTERIOR_STALE")
    assert bridge["posterior_age_seconds"] > bridge["max_lag_seconds"]
    assert "forecast_event_bridge" in result["failing_surfaces"]


def test_forecast_event_bridge_accepts_fsr_reemit_with_matching_posterior_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh FSR re-emit is healthy when it names an existing posterior identity."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    identity_hash = "posterior-identity-1"
    posterior_at = (now - timedelta(minutes=20)).isoformat()
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=posterior_at,
        fsr_created_at=(now - timedelta(minutes=1)).isoformat(),
        posterior_identity_hash=identity_hash,
        fsr_payload={
            "city": "Madrid",
            "target_date": "2026-07-09",
            "metric": "high",
            "source_run_id": identity_hash,
            "snapshot_hash": identity_hash,
            "cycle": "2026-07-08T06:00:00+00:00",
            "available_at": posterior_at,
            "captured_at": posterior_at,
        },
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    bridge = result["surfaces"]["forecast_event_bridge"]
    assert bridge["ok"] is True
    assert bridge["bridge_mode"] == "fsr_identity_match"
    assert bridge["latest_fsr_identity"] == identity_hash
    assert "forecast_event_bridge" not in result["failing_surfaces"]


def test_forecast_event_bridge_rejects_superseded_matching_posterior_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching FSR identity is stale when a newer live posterior supersedes it."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    identity_hash = "posterior-identity-old"
    old_posterior_at = (now - timedelta(minutes=20)).isoformat()
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=old_posterior_at,
        fsr_created_at=(now - timedelta(minutes=16)).isoformat(),
        posterior_identity_hash=identity_hash,
        fsr_payload={
            "city": "Madrid",
            "target_date": "2026-07-09",
            "metric": "high",
            "source_run_id": identity_hash,
            "snapshot_hash": identity_hash,
            "cycle": "2026-07-08T06:00:00+00:00",
            "available_at": old_posterior_at,
            "captured_at": old_posterior_at,
        },
    )
    forecast_conn = sqlite3.connect(sd / "zeus-forecasts.db")
    try:
        forecast_conn.execute(
            "INSERT INTO forecast_posteriors ("
            "computed_at, runtime_layer, posterior_identity_hash, city, target_date, "
            "temperature_metric, source_cycle_time, source_available_at"
            ") VALUES (?, 'live', ?, ?, ?, ?, ?, ?)",
            (
                (now - timedelta(minutes=2)).isoformat(),
                "posterior-identity-new",
                "Madrid",
                "2026-07-09",
                "high",
                "2026-07-08T12:00:00+00:00",
                (now - timedelta(minutes=3)).isoformat(),
            ),
        )
        forecast_conn.commit()
    finally:
        forecast_conn.close()

    result = compute_composite_live_health(state_dir=sd, now=now)

    bridge = result["surfaces"]["forecast_event_bridge"]
    assert bridge["ok"] is False
    assert bridge["issue"].startswith("FORECAST_EVENT_POSTERIOR_IDENTITY_SUPERSEDED")
    assert bridge["latest_fsr_identity"] == identity_hash
    assert "forecast_event_bridge" in result["failing_surfaces"]


def test_forecast_event_bridge_does_not_cross_supersede_unrelated_family(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer posterior for another market family cannot supersede this FSR."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    identity_hash = "madrid-posterior-identity"
    madrid_at = (now - timedelta(minutes=20)).isoformat()
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=madrid_at,
        fsr_created_at=(now - timedelta(minutes=1)).isoformat(),
        posterior_identity_hash=identity_hash,
        fsr_payload={
            "city": "Madrid",
            "target_date": "2026-07-09",
            "metric": "high",
            "source_run_id": identity_hash,
            "cycle": "2026-07-08T06:00:00+00:00",
            "available_at": madrid_at,
        },
    )
    forecast_conn = sqlite3.connect(sd / "zeus-forecasts.db")
    try:
        forecast_conn.execute(
            "INSERT INTO forecast_posteriors ("
            "computed_at, runtime_layer, posterior_identity_hash, city, target_date, "
            "temperature_metric, source_cycle_time, source_available_at"
            ") VALUES (?, 'live', ?, 'Taipei', '2026-07-12', 'high', ?, ?)",
            (
                (now - timedelta(minutes=2)).isoformat(),
                "taipei-newer-but-unrelated",
                "2026-07-08T12:00:00+00:00",
                (now - timedelta(minutes=3)).isoformat(),
            ),
        )
        forecast_conn.commit()
    finally:
        forecast_conn.close()

    result = compute_composite_live_health(state_dir=sd, now=now)

    bridge = result["surfaces"]["forecast_event_bridge"]
    assert bridge["ok"] is True
    assert bridge["bridge_mode"] == "fsr_identity_match"
    assert bridge["latest_fsr_identity_to_latest_posterior_lag_seconds"] == 0
    assert bridge["latest_fsr_family_latest_posterior_computed_at"] == madrid_at
    assert "forecast_event_bridge" not in result["failing_surfaces"]


def test_entry_q_version_not_evaluated_without_attested_main_daemon(
    tmp_path: Path,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write_entry_q_version_db(
        sd,
        [
            {
                "command_id": "cmd-missing-q",
                "state": "ACKED",
                "created_at": _now_iso(-30),
                "q_version": None,
            }
        ],
    )

    result = compute_composite_live_health(state_dir=sd)

    surface = result["surfaces"]["entry_q_version"]
    assert surface["ok"] is True
    assert surface["issue"] == "NOT_EVALUATED_MAIN_DAEMON_NOT_ATTESTED"
    assert "entry_q_version" not in result["failing_surfaces"]


def test_entry_q_version_degrades_when_recent_entry_lacks_q_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_entry_q_version_db(
        sd,
        [
            {
                "command_id": "cmd-missing-q",
                "state": "ACKED",
                "created_at": (now - timedelta(seconds=30)).isoformat(),
                "q_version": "",
            },
            {
                "command_id": "cmd-has-q",
                "state": "ACKED",
                "created_at": (now - timedelta(seconds=20)).isoformat(),
                "q_version": "q-live-001",
            },
        ],
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["entry_q_version"]
    assert surface["ok"] is False
    assert surface["issue"] == "ENTRY_Q_VERSION_MISSING:n=1"
    assert surface["missing_q_version_count"] == 1
    assert surface["missing_q_version_sample"][0]["command_id"] == "cmd-missing-q"
    assert "entry_q_version" in result["failing_surfaces"]


def test_entry_q_version_degrades_when_recent_terminal_entry_lacks_q_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_entry_q_version_db(
        sd,
        [
            {
                "command_id": "cmd-terminal-missing-q",
                "state": "CANCELLED",
                "created_at": (now - timedelta(seconds=30)).isoformat(),
                "q_version": None,
            }
        ],
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["entry_q_version"]
    assert surface["ok"] is False
    assert surface["issue"] == "ENTRY_Q_VERSION_MISSING:n=1"
    assert surface["missing_q_version_sample"][0]["state"] == "CANCELLED"
    assert "entry_q_version" in result["failing_surfaces"]


def test_entry_q_version_ignores_legacy_missing_identity_outside_lookback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_entry_q_version_db(
        sd,
        [
            {
                "command_id": "cmd-old-missing-q",
                "state": "CANCELLED",
                "created_at": (
                    now
                    - timedelta(seconds=live_health.ENTRY_Q_VERSION_LOOKBACK_SECONDS + 60)
                ).isoformat(),
                "q_version": None,
            },
            {
                "command_id": "cmd-recent-has-q",
                "state": "ACKED",
                "created_at": (now - timedelta(seconds=30)).isoformat(),
                "q_version": "q-live-002",
            },
        ],
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["entry_q_version"]
    assert surface["ok"] is True
    assert surface["missing_q_version_count"] == 0
    assert "entry_q_version" not in result["failing_surfaces"]


def test_entry_q_version_degrades_when_active_exposure_lacks_q_identity_outside_lookback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_entry_q_version_db(
        sd,
        [
            {
                "command_id": "cmd-old-active-missing-q",
                "position_id": "pos-active-missing-q",
                "state": "CANCELLED",
                "created_at": (
                    now
                    - timedelta(seconds=live_health.ENTRY_Q_VERSION_LOOKBACK_SECONDS + 60)
                ).isoformat(),
                "q_version": None,
            },
            {
                "command_id": "cmd-old-terminal-missing-q",
                "position_id": "pos-terminal-missing-q",
                "state": "CANCELLED",
                "created_at": (
                    now
                    - timedelta(seconds=live_health.ENTRY_Q_VERSION_LOOKBACK_SECONDS + 60)
                ).isoformat(),
                "q_version": None,
            },
        ],
    )
    _write_position_current_rows(
        sd,
        [
            {
                "position_id": "pos-active-missing-q",
                "phase": "active",
                "order_status": "partial",
                "shares": 11.627905,
                "chain_shares": 11.6279,
            },
            {
                "position_id": "pos-terminal-missing-q",
                "phase": "economically_closed",
                "order_status": "sell_filled",
                "shares": 0.0,
                "chain_shares": 0.0,
            },
        ],
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["entry_q_version"]
    assert surface["ok"] is False
    assert surface["issue"] == "ENTRY_Q_VERSION_MISSING_ACTIVE_EXPOSURE:n=1"
    assert surface["missing_q_version_count"] == 0
    assert surface["active_exposure_evaluated"] is True
    assert surface["active_missing_q_version_count"] == 1
    assert surface["active_missing_q_version_sample"][0]["position_id"] == "pos-active-missing-q"
    assert "entry_q_version" in result["failing_surfaces"]


def test_entry_q_version_active_missing_identity_reconstructs_final_intent_q(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    snapshot_id = "ems2-active-missing-q"
    _write_entry_q_version_db(
        sd,
        [
            {
                "command_id": "cmd-old-active-missing-q",
                "position_id": "pos-active-missing-q",
                "state": "CANCELLED",
                "created_at": (
                    now
                    - timedelta(seconds=live_health.ENTRY_Q_VERSION_LOOKBACK_SECONDS + 60)
                ).isoformat(),
                "q_version": None,
            },
        ],
    )
    _attach_entry_snapshot_id(
        sd,
        command_id="cmd-old-active-missing-q",
        snapshot_id=snapshot_id,
    )
    _write_position_current_rows(
        sd,
        [
            {
                "position_id": "pos-active-missing-q",
                "phase": "active",
                "order_status": "partial",
                "shares": 2.0,
                "chain_shares": 2.0,
            },
        ],
    )
    _write_final_intent_certificate(
        sd,
        snapshot_id=snapshot_id,
        posterior_identity_hash="posterior-hash-active-missing-q",
        q_live=0.82,
        q_lcb_5pct=0.71,
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["entry_q_version"]
    assert surface["ok"] is False
    assert surface["issue"] == "ENTRY_Q_VERSION_MISSING_ACTIVE_EXPOSURE:n=1"
    reconstruction = surface["active_missing_q_version_reconstruction_sample"][0]
    assert (
        reconstruction["reconstruction_status"]
        == "reconstructed_from_final_intent_certificate"
    )
    assert reconstruction["snapshot_id"] == snapshot_id
    assert reconstruction["executable_snapshot_id"] == snapshot_id
    assert reconstruction["posterior_identity_hash"] == "posterior-hash-active-missing-q"
    assert reconstruction["q_live"] == 0.82
    assert reconstruction["q_lcb_5pct"] == 0.71
    assert "entry_q_version" in result["failing_surfaces"]


def test_entry_q_version_active_missing_identity_reconstructs_via_certificate_edge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    decision_id = "edli_exec_cmd:test-edge-chain"
    _write_entry_q_version_db(
        sd,
        [
            {
                "command_id": "cmd-edge-chain",
                "position_id": "pos-edge-chain",
                "state": "CANCELLED",
                "created_at": (
                    now
                    - timedelta(seconds=live_health.ENTRY_Q_VERSION_LOOKBACK_SECONDS + 60)
                ).isoformat(),
                "q_version": None,
            },
        ],
    )
    _attach_entry_snapshot_id(
        sd,
        command_id="cmd-edge-chain",
        snapshot_id="venue-snapshot-not-in-final-intent",
    )
    _attach_entry_decision_id(
        sd,
        command_id="cmd-edge-chain",
        decision_id=decision_id,
    )
    _write_position_current_rows(
        sd,
        [
            {
                "position_id": "pos-edge-chain",
                "phase": "active",
                "order_status": "partial",
                "shares": 2.0,
                "chain_shares": 2.0,
            },
        ],
    )
    _write_execution_to_final_intent_edge(
        sd,
        decision_id=decision_id,
        final_executable_snapshot_id="final-intent-only-snapshot",
        posterior_identity_hash="posterior-hash-from-edge",
        q_live=0.86,
        q_lcb_5pct=0.81,
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["entry_q_version"]
    assert surface["ok"] is False
    assert surface["issue"] == "ENTRY_Q_VERSION_MISSING_ACTIVE_EXPOSURE:n=1"
    reconstruction = surface["active_missing_q_version_reconstruction_sample"][0]
    assert reconstruction["reconstruction_status"] == "reconstructed_from_final_intent_edge"
    assert reconstruction["decision_id"] == decision_id
    assert reconstruction["snapshot_id"] == "venue-snapshot-not-in-final-intent"
    assert reconstruction["executable_snapshot_id"] == "final-intent-only-snapshot"
    assert reconstruction["posterior_identity_hash"] == "posterior-hash-from-edge"
    assert reconstruction["q_live"] == 0.86
    assert reconstruction["q_lcb_5pct"] == 0.81
    assert reconstruction["execution_certificate_id"] == "ExecutionCommandCertificate:test"
    assert "entry_q_version" in result["failing_surfaces"]


def test_entry_q_version_ignores_pre_boot_missing_identity_inside_lookback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    boot_at = now - timedelta(seconds=60)
    loaded_sha = json.loads((sd / "loaded_sha.json").read_text())
    loaded_sha["generated_at"] = boot_at.isoformat()
    _write(sd / "loaded_sha.json", loaded_sha)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_entry_q_version_db(
        sd,
        [
            {
                "command_id": "cmd-pre-boot-missing-q",
                "state": "CANCELLED",
                "created_at": (boot_at - timedelta(seconds=30)).isoformat(),
                "q_version": None,
            },
            {
                "command_id": "cmd-post-boot-has-q",
                "state": "ACKED",
                "created_at": (boot_at + timedelta(seconds=10)).isoformat(),
                "q_version": "q-live-after-reload",
            },
        ],
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["entry_q_version"]
    assert surface["ok"] is True
    assert surface["boot_cutoff_used"] is True
    assert surface["missing_q_version_count"] == 0
    assert "entry_q_version" not in result["failing_surfaces"]


def test_entry_q_version_missing_column_degrades_when_main_daemon_attested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_entry_q_version_db(
        sd,
        [{"command_id": "cmd-no-column", "created_at": (now - timedelta(seconds=30)).isoformat()}],
        include_q_version_column=False,
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["entry_q_version"]
    assert surface["ok"] is False
    assert surface["issue"] == "ENTRY_Q_VERSION_COLUMN_MISSING:q_version"
    assert "entry_q_version" in result["failing_surfaces"]


def test_pending_exit_without_exit_command_yields_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare exit_intent with no EXIT command is a live health blocker."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_pending_exit_no_exit_command_db(sd, now=now)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["pending_exit_release_loop"]
    assert surface["ok"] is False
    assert surface["issue"] == "PENDING_EXIT_NO_EXIT_COMMAND:n=1"
    assert surface["pending_exit_no_command_count"] == 1
    assert surface["pending_exit_no_command_sample"][0]["position_id"] == "pos-no-exit-command"
    assert surface["pending_exit_no_command_sample"][0]["exit_reason"] == "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
    assert "pending_exit_release_loop" in result["failing_surfaces"]


def test_pending_exit_release_loop_yields_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated pending_exit release into EXIT_INTENT churn is a live health blocker."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_pending_exit_release_loop_db(sd, now=now)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["pending_exit_release_loop"]
    assert surface["ok"] is False
    assert surface["issue"] == "PENDING_EXIT_RELEASE_LOOP:n=1"
    assert surface["pending_exit_release_loop_count"] == 1
    assert surface["pending_exit_release_loop_sample"][0]["position_id"] == "pos-loop"
    assert surface["pending_exit_release_loop_sample"][0]["release_count"] == 2
    assert "pending_exit_release_loop" in result["failing_surfaces"]


def test_pending_exit_reassert_loop_yields_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated exit intents must not leave canonical state looking held."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_pending_exit_reassert_loop_db(sd, now=now)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["pending_exit_release_loop"]
    assert surface["ok"] is False
    assert surface["issue"] == "PENDING_EXIT_REASSERT_LOOP:n=1"
    assert surface["pending_exit_reassert_loop_count"] == 1
    assert surface["pending_exit_reassert_loop_sample"][0]["position_id"] == "pos-reassert"
    assert surface["pending_exit_reassert_loop_sample"][0]["reassert_exit_intent_count"] == 3
    assert surface["pending_exit_reassert_loop_sample"][0]["latest_reassert_at"] < (
        surface["pending_exit_reassert_loop_sample"][0]["latest_held_refresh_at"]
    )
    assert "pending_exit_release_loop" in result["failing_surfaces"]


def test_pending_exit_projection_regression_yields_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected exit must not be silently overwritten back to held projection."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_pending_exit_projection_regression_db(sd, now=now)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["pending_exit_release_loop"]
    assert surface["ok"] is False
    assert surface["issue"] == "PENDING_EXIT_PROJECTION_REGRESSION:n=1"
    assert surface["pending_exit_projection_regression_count"] == 1
    sample = surface["pending_exit_projection_regression_sample"][0]
    assert sample["position_id"] == "pos-regression"
    assert sample["latest_exit_event_type"] == "EXIT_ORDER_REJECTED"
    assert sample["latest_exit_status"] == "backoff_exhausted"
    assert sample["post_exit_held_event_count"] == 2
    assert "below min_order_size" in sample["latest_exit_error"]
    assert "pending_exit_release_loop" in result["failing_surfaces"]


def test_pending_exit_projection_regression_evaluates_without_attested_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB-backed exit projection regressions stay visible after the daemon dies."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": False,
            "issue": "MAIN_DAEMON_PROCESS_MISSING",
            "attested": False,
            "pid": 123,
        },
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_pending_exit_projection_regression_db(sd, now=now)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["pending_exit_release_loop"]
    assert surface["ok"] is False
    assert surface["issue"] == "PENDING_EXIT_PROJECTION_REGRESSION:n=1"
    assert surface["main_daemon_attested"] is False
    assert surface["main_daemon_issue"] == "MAIN_DAEMON_PROCESS_MISSING"
    assert "main_daemon" in result["failing_surfaces"]
    assert "pending_exit_release_loop" in result["failing_surfaces"]


def test_pending_exit_projection_release_then_held_is_not_regression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A canonical retry release authorizes the subsequent held projection."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_pending_exit_projection_regression_db(sd, now=now, released=True)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["pending_exit_release_loop"]
    assert surface["pending_exit_projection_regression_count"] == 0
    assert surface["pending_exit_projection_regression_sample"] == []
    assert surface["ok"] is True
    assert "pending_exit_release_loop" not in result["failing_surfaces"]

    # A release is not a permanent exemption. A newer exit transition becomes latest and
    # a subsequent held projection without another release must fail again.
    trade_conn = sqlite3.connect(sd / "zeus_trades.db")
    try:
        for seq, event_type, before, after, status in (
            (15, "EXIT_INTENT", "day0_window", "pending_exit", "exit_intent"),
            (16, "EXIT_ORDER_REJECTED", "day0_window", "pending_exit", "retry_pending"),
            (17, "MONITOR_REFRESHED", "day0_window", "day0_window", "partial"),
        ):
            trade_conn.execute(
                "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pos-regression",
                    seq,
                    event_type,
                    (now - timedelta(minutes=1, seconds=17 - seq)).isoformat(),
                    before,
                    after,
                    status,
                    json.dumps({"status": status}),
                ),
            )
        trade_conn.commit()
    finally:
        trade_conn.close()

    result = compute_composite_live_health(state_dir=sd, now=now)
    surface = result["surfaces"]["pending_exit_release_loop"]
    assert surface["pending_exit_projection_regression_count"] == 1
    assert surface["pending_exit_projection_regression_sample"][0][
        "latest_exit_event_type"
    ] == "EXIT_ORDER_REJECTED"


def test_pending_exit_runtime_gate_block_yields_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real exit decision blocked by runtime submit gate must stay visible."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_pending_exit_runtime_gate_block_db(sd, now=now)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["pending_exit_release_loop"]
    assert surface["ok"] is False
    assert surface["issue"] == "PENDING_EXIT_RUNTIME_GATE_BLOCK:n=1"
    assert surface["pending_exit_runtime_gate_block_count"] == 1
    sample = surface["pending_exit_runtime_gate_block_sample"][0]
    assert sample["position_id"] == "pos-runtime-gate"
    assert sample["runtime_gate_reject_count"] == 2
    assert sample["latest_runtime_gate_status"] == "retry_pending"
    assert sample["latest_runtime_gate_error"] == "structured_runtime_gate_block_without_legacy_text"
    assert "pending_exit_release_loop" in result["failing_surfaces"]


def test_pending_exit_multiple_failure_issue_keeps_all_active_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_pending_exit_no_exit_command_db(sd, now=now)
    _write_pending_exit_projection_regression_db(sd, now=now)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["pending_exit_release_loop"]
    assert surface["ok"] is False
    assert surface["issue"] == (
        "PENDING_EXIT_MULTIPLE_FAILURES:"
        "no_exit_command=1:projection_regression=1"
    )
    assert surface["pending_exit_no_command_count"] == 1
    assert surface["pending_exit_projection_regression_count"] == 1
    assert "pending_exit_release_loop" in result["failing_surfaces"]


def test_pending_exit_historical_churn_reports_stabilized_non_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """High-frequency same-day exit churn stays visible after it has quieted."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_pending_exit_historical_churn_db(sd, now=now)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["pending_exit_release_loop"]
    assert surface["ok"] is True
    assert surface["issue"] is None
    assert surface["pending_exit_release_loop_count"] == 0
    assert surface["pending_exit_reassert_loop_count"] == 0
    assert surface["pending_exit_churn_count"] == 0
    assert surface["pending_exit_churn_total_count"] == 1
    assert surface["pending_exit_churn_historical_stabilized_count"] == 1
    historical = surface["pending_exit_churn_historical_stabilized_sample"][0]
    assert historical["position_id"] == "pos-churn"
    assert historical["exit_intent_count"] == 12
    assert historical["exit_rejection_count"] == 6
    assert historical["exit_release_count"] == 4
    assert "pending_exit_release_loop" not in result["failing_surfaces"]


def test_pending_exit_current_churn_yields_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recent exit churn remains a live health blocker even with historical context."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_pending_exit_current_churn_db(sd, now=now)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["pending_exit_release_loop"]
    assert surface["ok"] is False
    assert surface["issue"] == "PENDING_EXIT_CHURN:n=1"
    assert surface["pending_exit_churn_count"] == 1
    assert surface["pending_exit_churn_total_count"] == 1
    assert surface["pending_exit_churn_sample"][0]["position_id"] == "pos-churn"
    assert surface["pending_exit_churn_sample"][0]["exit_intent_count"] == 13
    assert "pending_exit_release_loop" in result["failing_surfaces"]


def test_monitor_probability_freshness_degrades_when_latest_active_monitor_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_monitor_probability_freshness_db(sd, now=now, latest_event_fresh=False)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["monitor_probability_freshness"]
    assert surface["ok"] is False
    assert surface["issue"] == "MONITOR_PROBABILITY_STALE_LATEST:n=1"
    assert surface["latest_stale_monitor_sample"][0]["position_id"] == "pos-monitor"
    assert "monitor_probability_freshness" in result["failing_surfaces"]


def test_monitor_probability_freshness_allows_resolved_recent_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_monitor_probability_freshness_db(sd, now=now, latest_event_fresh=True)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["monitor_probability_freshness"]
    assert surface["ok"] is True
    assert surface["latest_stale_monitor_count"] == 0
    assert "monitor_probability_freshness" not in result["failing_surfaces"]


def test_monitor_probability_freshness_degrades_on_unconditioned_daily_extrema_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_monitor_probability_freshness_db(
        sd,
        now=now,
        latest_event_fresh=True,
        day0_daily_extrema_receipt="unconditioned",
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["monitor_probability_freshness"]
    assert surface["ok"] is False
    assert surface["issue"] == (
        "MONITOR_DAY0_DAILY_EXTREMA_USED_AS_REMAINING_WINDOW:n=1"
    )
    assert surface["day0_daily_extrema_unconditioned_count"] == 1
    sample = surface["day0_daily_extrema_unconditioned_sample"][0]
    assert sample["position_id"] == "pos-monitor"
    assert sample["selected_method"] == "day0_observation_remaining_window"
    assert sample["remaining_window_source"] == "day0_raw_model_extrema"
    assert "monitor_probability_freshness" in result["failing_surfaces"]


def test_monitor_probability_freshness_allows_conditioned_daily_extrema_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_forecast_event_bridge_dbs(
        sd,
        posterior_computed_at=(now - timedelta(seconds=30)).isoformat(),
        fsr_created_at=(now - timedelta(seconds=20)).isoformat(),
    )
    _write_monitor_probability_freshness_db(
        sd,
        now=now,
        latest_event_fresh=True,
        day0_daily_extrema_receipt="conditioned",
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["monitor_probability_freshness"]
    assert surface["ok"] is True
    assert surface["day0_daily_extrema_unconditioned_count"] == 0
    assert "monitor_probability_freshness" not in result["failing_surfaces"]


def test_monitor_probability_freshness_degrades_when_latest_monitor_too_old(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )
    monkeypatch.setattr(
        live_health,
        "_process_code_surface",
        lambda main_daemon_surface: {"ok": True, "issue": None, "evaluated": True},
    )
    now = datetime.now(timezone.utc)
    _write_monitor_probability_freshness_db(
        sd,
        now=now,
        latest_event_fresh=True,
        stale_event_age=timedelta(minutes=21),
        latest_event_age=timedelta(minutes=20),
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["monitor_probability_freshness"]
    assert surface["ok"] is False
    assert surface["issue"] == "MONITOR_PROBABILITY_STALE_AGE:n=1"
    assert surface["latest_monitor_age_stale_count"] == 1
    sample = surface["latest_monitor_age_stale_sample"][0]
    assert sample["position_id"] == "pos-monitor"
    assert sample["last_monitor_prob_is_fresh"] == 1
    assert sample["latest_monitor_age_seconds"] == pytest.approx(20 * 60)
    assert sample["latest_monitor_stale_overage_seconds"] == pytest.approx(10 * 60)
    assert "monitor_probability_freshness" in result["failing_surfaces"]


def test_monitor_probability_freshness_evaluates_stale_age_without_attested_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": False,
            "issue": "MAIN_DAEMON_PROCESS_NOT_FOUND",
            "attested": False,
            "pid": 999999,
        },
    )
    now = datetime.now(timezone.utc)
    _write_monitor_probability_freshness_db(
        sd,
        now=now,
        latest_event_fresh=True,
        stale_event_age=timedelta(minutes=21),
        latest_event_age=timedelta(minutes=20),
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["monitor_probability_freshness"]
    assert surface["ok"] is False
    assert surface["issue"] == "MONITOR_PROBABILITY_STALE_AGE:n=1"
    assert surface["main_daemon_attested"] is False
    assert surface["main_daemon_issue"] == "MAIN_DAEMON_PROCESS_NOT_FOUND"
    assert "main_daemon" in result["failing_surfaces"]
    assert "monitor_probability_freshness" in result["failing_surfaces"]


def test_sub_min_partial_position_degrades_when_held_shares_below_snapshot_minimum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    now = datetime.now(timezone.utc)
    _write_sub_min_partial_position_db(sd, now=now, chain_shares=3.8)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["sub_min_partial_position"]
    assert surface["ok"] is False
    assert surface["issue"] == "SUB_MIN_PARTIAL_POSITION_UNEXITABLE:n=1"
    assert surface["sub_min_partial_position_count"] == 1
    sample = surface["sub_min_partial_position_sample"][0]
    assert sample["position_id"] == "pos-sub-min"
    assert sample["held_shares"] == pytest.approx(3.8)
    assert sample["min_order_size"] == "5"
    assert sample["orderbook_top_ask"] == "ABSENT"
    assert "sub_min_partial_position" in result["failing_surfaces"]


def test_sub_min_partial_position_allows_size_at_snapshot_minimum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    now = datetime.now(timezone.utc)
    _write_sub_min_partial_position_db(sd, now=now, chain_shares=5.0)

    result = compute_composite_live_health(state_dir=sd, now=now)

    surface = result["surfaces"]["sub_min_partial_position"]
    assert surface["ok"] is True
    assert surface["sub_min_partial_position_count"] == 0
    assert "sub_min_partial_position" not in result["failing_surfaces"]


def test_day0_decision_trace_degrades_when_processed_day0_has_no_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write_day0_trace_dbs(sd, with_regret=False)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    trace = result["surfaces"]["day0_decision_trace"]
    assert trace["ok"] is False
    assert "DAY0_PROCESSED_WITHOUT_DECISION_TRACE" in trace["issue"]
    assert trace["missing_trace_count"] == 1
    assert "day0_decision_trace" in result["failing_surfaces"]


def test_day0_decision_trace_accepts_processed_day0_with_regret_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write_day0_trace_dbs(sd, with_regret=True)
    monkeypatch.setattr(
        live_health,
        "_main_daemon_surface",
        lambda status_summary, heartbeat: {
            "ok": True,
            "issue": None,
            "attested": True,
            "pid": 123,
            "command": "python -m src.main",
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    trace = result["surfaces"]["day0_decision_trace"]
    assert trace["ok"] is True
    assert trace["processed_event_count"] == 1
    assert trace["traced_processed_event_count"] == 1
    assert "day0_decision_trace" not in result["failing_surfaces"]


def test_forecast_decision_trace_degrades_when_processed_fsr_has_no_artifact(
    tmp_path: Path,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _write_forecast_trace_dbs(sd, with_no_submit=False)

    trace = live_health._forecast_decision_trace_surface(
        sd,
        datetime.now(timezone.utc),
        main_daemon_surface={"attested": True},
    )

    assert trace["ok"] is False
    assert "FORECAST_PROCESSED_WITHOUT_DECISION_TRACE" in trace["issue"]
    assert trace["missing_trace_count"] == 1
    assert trace["missing_trace_sample"][0]["event_id"] == "fsr-trace-1"


def test_forecast_decision_trace_accepts_processed_fsr_with_no_submit_artifact(
    tmp_path: Path,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _write_forecast_trace_dbs(sd, with_no_submit=True)

    trace = live_health._forecast_decision_trace_surface(
        sd,
        datetime.now(timezone.utc),
        main_daemon_surface={"attested": True},
    )

    assert trace["ok"] is True
    assert trace["processed_event_count"] == 1
    assert trace["traced_processed_event_count"] == 1


def test_high_yes_edge_degrades_without_yes_action_or_rejection_trace(
    tmp_path: Path,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _write_high_yes_edge_dbs(sd, with_yes_no_submit=False)

    surface = live_health._high_yes_edge_missed_surface(
        sd,
        datetime.now(timezone.utc),
        main_daemon_surface={
            "attested": False,
            "issue": "MAIN_DAEMON_PROCESS_NOT_FOUND",
        },
    )

    assert surface["ok"] is False
    assert surface["issue"] == "HIGH_YES_EDGE_WITHOUT_FSR:n=1"
    assert surface["main_daemon_attested"] is False
    assert surface["main_daemon_issue"] == "MAIN_DAEMON_PROCESS_NOT_FOUND"
    assert surface["high_yes_edge_count"] == 1
    assert surface["very_high_yes_edge_count"] == 1
    assert surface["missing_fsr_high_yes_edge_count"] == 1
    assert surface["recent_buy_yes_no_submit_count"] == 0
    assert surface["recent_buy_yes_no_trade_count"] == 0
    assert surface["missed_high_yes_edge_sample"][0]["condition_id"] == "cond-high-yes-1"


def test_high_yes_edge_ignores_stale_executable_quote(
    tmp_path: Path,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _write_high_yes_edge_dbs(sd, stale_quote=True)

    surface = live_health._high_yes_edge_missed_surface(
        sd,
        datetime.now(timezone.utc),
        main_daemon_surface={"attested": False},
    )

    assert surface["ok"] is True
    assert surface["high_yes_edge_count"] == 0
    assert surface["missed_high_yes_edge_count"] == 0


def test_high_yes_edge_accepts_buy_yes_no_submit_evidence(
    tmp_path: Path,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _write_high_yes_edge_dbs(sd, with_yes_no_submit=True)

    surface = live_health._high_yes_edge_missed_surface(
        sd,
        datetime.now(timezone.utc),
        main_daemon_surface={"attested": False},
    )

    assert surface["ok"] is True
    assert surface["high_yes_edge_count"] == 1
    assert surface["missed_high_yes_edge_count"] == 0
    assert surface["recent_buy_yes_no_submit_count"] == 1
    assert surface["recent_buy_yes_no_trade_count"] == 0


def test_high_yes_edge_degrades_when_quality_yes_no_trade_has_no_order_chain(
    tmp_path: Path,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _write_high_yes_edge_dbs(sd, with_yes_no_trade=True)

    surface = live_health._high_yes_edge_missed_surface(
        sd,
        datetime.now(timezone.utc),
        main_daemon_surface={"attested": False},
    )

    assert surface["ok"] is False
    assert surface["issue"] == "HIGH_YES_QUALITY_SUPPRESSED_WITHOUT_ORDER_CHAIN:n=1"
    assert surface["high_yes_edge_count"] == 1
    assert surface["missed_high_yes_edge_count"] == 0
    assert surface["recent_buy_yes_entry_command_count"] == 0
    assert surface["recent_buy_yes_no_submit_count"] == 0
    assert surface["recent_buy_yes_no_trade_count"] == 1
    assert surface["recent_buy_yes_high_quality_no_trade_count"] == 1
    high_quality = surface["recent_buy_yes_high_quality_no_trade_sample"][0]
    assert high_quality["q_lcb_5pct"] == 0.91
    assert high_quality["trade_score"] == 0.71
    assert high_quality["city"] == "Paris"
    reason_class = surface["recent_buy_yes_no_trade_top_reason_classes"][0]
    assert reason_class["rejection_stage"] == "TRADE_SCORE"
    assert reason_class["rejection_reason_class"] == (
        "EVENT_BOUND_CANDIDATE_REJECTED:"
        "QKERNEL_EXECUTION_ECONOMICS_FALSE_EDGE_RATE_BLOCKS:"
        "value=0.500000:alpha=0.100000"
    )
    assert reason_class["count"] == 1


def test_high_yes_edge_separates_degenerate_day0_lcb_from_quality_yes(
    tmp_path: Path,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _write_high_yes_edge_dbs(sd, with_degenerate_day0_lcb_no_trade=True)

    surface = live_health._high_yes_edge_missed_surface(
        sd,
        datetime.now(timezone.utc),
        main_daemon_surface={"attested": False},
    )

    assert surface["ok"] is True
    assert surface["recent_buy_yes_no_trade_count"] == 1
    assert surface["recent_buy_yes_high_quality_no_trade_count"] == 0
    assert surface["recent_buy_yes_high_quality_no_trade_sample"] == []
    assert surface["recent_buy_yes_degenerate_day0_lcb_no_trade_count"] == 1
    degenerate = surface["recent_buy_yes_degenerate_day0_lcb_no_trade_sample"][0]
    assert "degenerate with q_live" in degenerate["rejection_reason"]
    assert degenerate["q_lcb_5pct"] == 0.91


def test_high_yes_edge_accepts_quality_yes_with_order_chain_evidence(
    tmp_path: Path,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _write_high_yes_edge_dbs(
        sd,
        with_yes_no_trade=True,
        with_yes_entry_command=True,
    )

    surface = live_health._high_yes_edge_missed_surface(
        sd,
        datetime.now(timezone.utc),
        main_daemon_surface={"attested": False},
    )

    assert surface["ok"] is True
    assert surface["high_yes_edge_count"] == 1
    assert surface["missed_high_yes_edge_count"] == 0
    assert surface["recent_buy_yes_entry_command_count"] == 1
    assert surface["recent_buy_yes_no_trade_count"] == 1
    assert surface["recent_buy_yes_high_quality_no_trade_count"] == 1


def test_high_yes_edge_ignores_no_trade_before_current_posterior(
    tmp_path: Path,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _write_high_yes_edge_dbs(sd, with_stale_yes_no_trade=True)

    surface = live_health._high_yes_edge_missed_surface(
        sd,
        datetime.now(timezone.utc),
        main_daemon_surface={"attested": False},
    )

    assert surface["ok"] is False
    assert surface["issue"] == "HIGH_YES_EDGE_WITHOUT_FSR:n=1"
    assert surface["missed_high_yes_edge_count"] == 1


def test_bpf_capture_failed_yields_forecast_pipeline_degraded(tmp_path: Path) -> None:
    sd = tmp_path
    _setup_healthy_state(sd)
    health_path = sd / "scheduler_jobs_health.json"
    scheduler = json.loads(health_path.read_text())
    scheduler["bayes_precision_fusion_capture"] = {
        "status": "FAILED",
        "last_failure_reason": "global models unavailable",
        "last_run_at": _now_iso(-5),
    }
    _write(health_path, scheduler)

    result = compute_composite_live_health(state_dir=sd)

    assert result["healthy"] is False
    assert result["status"] == "DEGRADED"
    assert "forecast_pipeline" in result["failing_surfaces"]
    assert result["surfaces"]["forecast_pipeline"]["ok"] is False
    assert "bayes_precision_fusion_capture" in (
        result["surfaces"]["forecast_pipeline"]["issue"] or ""
    )


# ---------------------------------------------------------------------------
# T2: status_summary stale → DEGRADED
# ---------------------------------------------------------------------------

def test_stale_status_summary_yields_degraded(tmp_path: Path) -> None:
    """T2: status_summary older than 5 min makes composite DEGRADED."""
    sd = tmp_path / "state"
    sd.mkdir()

    _setup_healthy_state(sd)
    # Overwrite status_summary with a stale timestamp (>5 min ago)
    stale_offset = -(STATUS_FRESH_BUDGET_SECONDS + 60)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(stale_offset),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(stale_offset),
                "candidates": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["healthy"] is False, f"stale status_summary must yield DEGRADED: {result}"
    assert result["status"] == "DEGRADED"
    assert "status_summary" in result["failing_surfaces"]
    assert result["surfaces"]["status_summary"]["ok"] is False
    assert "STALE" in (result["surfaces"]["status_summary"]["issue"] or "")
    # heartbeat and run_mode should still show OK
    assert result["surfaces"]["heartbeat"]["ok"] is True
    assert result["surfaces"]["run_mode"]["ok"] is True


def test_status_summary_terminal_venue_fact_conflict_yields_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh status is not healthy if terminal local command truth conflicts with venue facts."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    status_path = sd / "status_summary.json"
    status = json.loads(status_path.read_text())
    status.setdefault("execution", {})["terminal_command_venue_fact_conflicts"] = {
        "count": 1,
        "orders": [
            {
                "command_id": "cmd-cancelled-but-live",
                "command_state": "CANCELLED",
                "venue_state": "LIVE",
                "venue_order_id": "0xabc",
                "remaining_size": 12.5,
            }
        ],
    }
    status_path.write_text(json.dumps(status))

    result = compute_composite_live_health(state_dir=sd)

    surface = result["surfaces"]["status_summary"]
    assert result["status"] == "DEGRADED"
    assert "status_summary" in result["failing_surfaces"]
    assert surface["ok"] is False
    assert surface["issue"] == "TERMINAL_COMMAND_VENUE_FACT_CONFLICT:n=1"
    assert surface["terminal_command_venue_fact_conflict_sample"][0]["command_id"] == (
        "cmd-cancelled-but-live"
    )


def test_status_summary_terminal_venue_fact_conflict_closed_phase_non_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settled/closed terminal command fact ambiguity is reported but not a current blocker."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    status_path = sd / "status_summary.json"
    status = json.loads(status_path.read_text())
    status.setdefault("execution", {})["terminal_command_venue_fact_conflicts"] = {
        "count": 1,
        "orders": [
            {
                "command_id": "cmd-settled-cancelled-but-partial",
                "command_state": "CANCELLED",
                "venue_state": "PARTIALLY_MATCHED",
                "venue_order_id": "0xabc",
                "remaining_size": 12.5,
                "phase": "settled",
            }
        ],
    }
    status_path.write_text(json.dumps(status))

    result = compute_composite_live_health(state_dir=sd)

    surface = result["surfaces"]["status_summary"]
    assert "status_summary" not in result["failing_surfaces"]
    assert surface["ok"] is True
    assert surface["issue"] is None
    assert surface["terminal_command_venue_fact_conflict_count"] == 0
    assert surface["terminal_command_venue_fact_conflict_total_count"] == 1
    assert surface["terminal_command_venue_fact_conflict_historical_count"] == 1
    assert (
        surface["terminal_command_venue_fact_conflict_historical_sample"][0]["command_id"]
        == "cmd-settled-cancelled-but-partial"
    )


# ---------------------------------------------------------------------------
# T3: all healthy → HEALTHY
# ---------------------------------------------------------------------------

def test_all_healthy_surfaces_yield_healthy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T3: when all three surfaces are fresh and OK, composite is HEALTHY."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())

    result = compute_composite_live_health(state_dir=sd)

    assert result["healthy"] is True, f"all-OK surfaces must yield HEALTHY: {result}"
    assert result["status"] == "HEALTHY"
    assert result["failing_surfaces"] == []
    for surface in (
        "heartbeat",
        "runtime_code",
        "main_daemon",
        "venue_heartbeat",
        "run_mode",
        "status_summary",
        "execution_capability",
    ):
        assert result["surfaces"][surface]["ok"] is True, (
            f"surface {surface!r} should be OK: {result['surfaces'][surface]}"
        )


# ---------------------------------------------------------------------------
# T4: DEGRADED emits WARNING log with failing surface name
# ---------------------------------------------------------------------------

def test_degraded_emits_warning_log_with_surface_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T4: DEGRADED composite emits WARNING log naming the failing surface."""
    sd = tmp_path / "state"
    sd.mkdir()

    # Use a stale heartbeat to trigger DEGRADED
    _write(
        sd / "daemon-heartbeat.json",
        {"alive": True, "timestamp": _now_iso(-(STATUS_FRESH_BUDGET_SECONDS + 120)), "mode": "live"},
    )
    _write(
        sd / "scheduler_jobs_health.json",
        {"_run_mode": {"status": "OK", "last_run_at": _now_iso(-30)}},
    )
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 0,
            },
        },
    )

    with caplog.at_level(logging.WARNING, logger="src.control.live_health"):
        result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    # Must emit at least one WARNING mentioning "heartbeat"
    warning_texts = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("heartbeat" in msg for msg in warning_texts), (
        f"No WARNING log mentioning 'heartbeat' found. Got: {warning_texts}"
    )
    # Must mention "DEGRADED" keyword
    assert any("DEGRADED" in msg for msg in warning_texts), (
        f"No WARNING log containing 'DEGRADED' found. Got: {warning_texts}"
    )


def test_business_plane_missing_candidate_counter_yields_degraded(tmp_path: Path) -> None:
    """F5: fresh process/status without cycle counters is not live progress proof."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "entry_orders_submitted": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert "business_plane" in result["failing_surfaces"]
    assert result["surfaces"]["business_plane"]["ok"] is False
    assert result["surfaces"]["business_plane"]["issue"] == "CANDIDATE_COUNTER_MISSING"


def test_business_plane_skipped_cycle_yields_degraded(tmp_path: Path) -> None:
    """F6: scheduler OK plus skipped cycle is daemon liveness, not business progress."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "skipped": True,
                "skip_reason": "cycle_lock_held",
                "candidates": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["business_plane"]["issue"] == "CYCLE_SKIPPED: cycle_lock_held"


def test_business_plane_zero_candidates_without_proof_yields_degraded(tmp_path: Path) -> None:
    """Zero candidates needs explicit no-market/source-freshness proof."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["business_plane"]["issue"] == (
        "ZERO_CANDIDATES_WITHOUT_SOURCE_OR_NO_MARKET_PROOF"
    )


def test_business_plane_zero_candidates_with_entry_gate_block_has_proof(tmp_path: Path) -> None:
    """A boot/entry gate block explains zero candidates; execution_capability carries the failure."""

    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    cycle_time = _now_iso(-30)
    capability = _healthy_execution_capability()
    capability["entry"] = {
        "status": "unavailable",
        "global_allow_submit": False,
        "components": [
            {
                "component": "live_boot_prerequisite",
                "allowed": False,
                "reason": "LIVE_SIDECAR_BOOT_BLOCKED: sidecar stale",
            }
        ],
        "unavailable_components": ["live_boot_prerequisite"],
    }
    _write(
        sd / "status_summary.json",
        {
            "timestamp": cycle_time,
            "cycle": {
                "mode": "boot_blocked",
                "completed_at": cycle_time,
                "candidates": 0,
                "entries_blocked_reason": "LIVE_SIDECAR_BOOT_BLOCKED: sidecar stale",
            },
            "execution_capability": capability,
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    business = result["surfaces"]["business_plane"]
    assert business["ok"] is True
    assert business["progress"]["entry_unavailable_proof"] is True
    assert business["progress"]["entry_unavailable_reason"] == (
        "live_boot_prerequisite:LIVE_SIDECAR_BOOT_BLOCKED: sidecar stale"
    )
    assert result["surfaces"]["execution_capability"]["ok"] is False
    assert "entry:live_boot_prerequisite" in (
        result["surfaces"]["execution_capability"]["issue"] or ""
    )


def test_business_plane_candidates_without_final_intent_need_no_trade_reasons(tmp_path: Path) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 3,
                "final_intents_built": 0,
                "no_trades": 3,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["business_plane"]["issue"] == (
        "CANDIDATES_WITHOUT_FINAL_INTENTS_OR_NO_TRADE_REASONS"
    )


def test_business_plane_all_no_trade_reasons_still_degrades_without_capital_flow(tmp_path: Path) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "edli_event_reactor",
                "completed_at": _now_iso(-30),
                "candidates": 12,
                "final_intents_built": 0,
                "submit_attempts": 0,
                "no_trades": 12,
                "top_no_trade_reasons": {"QKERNEL_SPINE_NO_TRADE:NO_POSITIVE_EDGE_CANDIDATE": 12},
            },
            "execution_capability": _healthy_execution_capability(),
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["business_plane"]["issue"] == (
        "CANDIDATES_ONLY_NO_TRADE_NO_CAPITAL_FLOW"
    )
    assert result["surfaces"]["business_plane"]["progress"]["no_trade_reason_proof"] is True


def test_business_plane_candidates_blocked_by_entry_gate_have_explicit_proof(tmp_path: Path) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    capability = _healthy_execution_capability()
    capability["entry"] = {
        "status": "unavailable",
        "global_allow_submit": False,
        "components": [
            {
                "component": "risk_allocator_global",
                "allowed": False,
                "reason": "reduce_only_mode_active",
            }
        ],
        "unavailable_components": ["risk_allocator_global"],
    }
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "control": {
                "entries_paused": True,
                "entries_pause_reason": "operator_pause_live_bad_entry_tokyo_005_yes_until_root_fix",
            },
            "cycle": {
                "mode": "edli_event_reactor",
                "completed_at": _now_iso(-30),
                "candidates": 310,
                "final_intents_built": 0,
                "no_trades": 310,
                "top_no_trade_reasons": {},
            },
            "execution_capability": capability,
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    business = result["surfaces"]["business_plane"]
    assert business["ok"] is True
    assert business["progress"]["entry_unavailable_proof"] is True
    assert business["progress"]["entry_unavailable_reason"] == (
        "operator_pause_live_bad_entry_tokyo_005_yes_until_root_fix"
    )
    assert result["surfaces"]["execution_capability"]["ok"] is False


def test_business_plane_final_intents_without_submit_attempts_yields_degraded(tmp_path: Path) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 3,
                "final_intents_built": 1,
                "entry_orders_submitted": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["business_plane"]["issue"] == (
        "FINAL_INTENTS_WITHOUT_SUBMIT_ATTEMPTS"
    )


def test_business_plane_submit_without_ack_or_rejection_yields_degraded(tmp_path: Path) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 3,
                "final_intents_built": 1,
                "entry_orders_submitted": 1,
                "venue_acks": 0,
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["business_plane"]["issue"] == (
        "SUBMIT_ATTEMPTS_WITHOUT_ACK_OR_DETERMINISTIC_REJECTION"
    )


def test_business_plane_submit_without_ack_allows_deterministic_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 3,
                "final_intents_built": 1,
                "entry_orders_submitted": 1,
                "venue_acks": 0,
                "deterministic_rejections": {"invalid_amount_precision": 1},
            },
            "execution_capability": _healthy_execution_capability(),
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "HEALTHY"
    assert result["surfaces"]["business_plane"]["progress"]["deterministic_rejection_observed"] is True


def test_business_plane_exposes_entry_and_reconcile_progress_counters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7: composite output exposes candidate/intent/submit/ack/reconcile truth."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 4,
                "final_intents_built": 1,
                "entry_orders_submitted": 1,
                "venue_acks": 1,
                "command_recovery": {"scanned": 3, "advanced": 1},
                "chain_sync": {"synced": 2},
            },
            "execution_capability": _healthy_execution_capability(),
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    progress = result["surfaces"]["business_plane"]["progress"]
    assert result["status"] == "HEALTHY"
    assert progress["candidate_evaluated"] is True
    assert progress["final_intent_built"] is True
    assert progress["submit_attempted"] is True
    assert progress["venue_ack_observed"] is True
    assert progress["reconcile_progress_observed"] is True


def test_business_plane_does_not_infer_venue_ack_from_submit_count(tmp_path: Path) -> None:
    """A submit attempt is not venue acknowledgement authority."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "status_summary.json",
        {
            "timestamp": _now_iso(-30),
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": _now_iso(-30),
                "candidates": 4,
                "final_intents_built": 1,
                "entry_orders_submitted": 1,
                "command_recovery": {"scanned": 1},
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    progress = result["surfaces"]["business_plane"]["progress"]
    assert progress["submit_attempted"] is True
    assert progress["venue_acks"] == 0
    assert progress["venue_ack_observed"] is False


def test_execution_capability_unavailable_yields_degraded(tmp_path: Path) -> None:
    """Fresh daemon/cycle signals cannot override the live order gate."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    cycle_time = _now_iso(-30)
    capability = _healthy_execution_capability()
    capability["entry"] = {
        "status": "unavailable",
        "global_allow_submit": False,
        "components": [
            {
                "component": "heartbeat_supervisor",
                "allowed": False,
                "reason": "PolyApiException[status_code=None, error_message=Request exception!]",
            },
            {
                "component": "risk_allocator_global",
                "allowed": False,
                "reason": "heartbeat_lost",
            },
        ],
        "unavailable_components": ["heartbeat_supervisor", "risk_allocator_global"],
    }
    _write(
        sd / "status_summary.json",
        {
            "timestamp": cycle_time,
            "cycle": {
                "mode": "opening_hunt",
                "completed_at": cycle_time,
                "candidates": 4,
                "final_intents_built": 0,
                "no_trades": 4,
                "top_no_trade_reasons": {"EDGE_INSUFFICIENT": 4},
            },
            "execution_capability": capability,
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert "execution_capability" in result["failing_surfaces"]
    assert result["surfaces"]["business_plane"]["ok"] is True
    assert result["surfaces"]["execution_capability"]["ok"] is False
    assert "entry:heartbeat_supervisor,risk_allocator_global" in (
        result["surfaces"]["execution_capability"]["issue"] or ""
    )


def test_execution_capability_reports_entry_and_reduce_only_exit_gates(
    tmp_path: Path,
) -> None:
    """Boot-blocked status must not collapse new-entry and reduce-only exit gates."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    cycle_time = _now_iso(-30)
    reason = "LIVE_SIDECAR_BOOT_BLOCKED: sidecar stale"
    _write(
        sd / "status_summary.json",
        {
            "timestamp": cycle_time,
            "cycle": {
                "mode": "boot_blocked",
                "completed_at": cycle_time,
                "candidates": 0,
                "entries_blocked_reason": reason,
            },
            "execution_capability": {
                "entry": {
                    "action": "entry",
                    "capability": "live_venue_submit",
                    "status": "unavailable",
                    "global_allow_submit": False,
                    "components": [
                        {
                            "component": "live_venue_submit:live_boot_prerequisite",
                            "capability": "live_venue_submit",
                            "allowed": False,
                            "reason": reason,
                        }
                    ],
                    "unavailable_components": [
                        "live_venue_submit:live_boot_prerequisite"
                    ],
                },
                "exit": {
                    "action": "exit",
                    "capability": "reduce_only_exit_submit",
                    "status": "unavailable",
                    "global_allow_submit": False,
                    "components": [
                        {
                            "component": "reduce_only_exit_submit:live_boot_prerequisite",
                            "capability": "reduce_only_exit_submit",
                            "allowed": False,
                            "reason": reason,
                        }
                    ],
                    "unavailable_components": [
                        "reduce_only_exit_submit:live_boot_prerequisite"
                    ],
                },
            },
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    surface = result["surfaces"]["execution_capability"]
    assert surface["ok"] is False
    assert surface["actions"]["entry"]["capability"] == "live_venue_submit"
    assert surface["actions"]["exit"]["capability"] == "reduce_only_exit_submit"
    assert "entry:live_venue_submit:live_boot_prerequisite" in (surface["issue"] or "")
    assert "exit:reduce_only_exit_submit:live_boot_prerequisite" in (
        surface["issue"] or ""
    )


def test_venue_heartbeat_lost_yields_degraded_even_when_daemon_heartbeat_is_fresh(
    tmp_path: Path,
) -> None:
    """Daemon liveness is not resting-order safety authority."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "venue-heartbeat-keeper.json",
        {
            "health": "LOST",
            "resting_order_safe": False,
            "written_at": _now_iso(-2),
            "cadence_seconds": 5,
            "last_error": "PolyApiException[status_code=None, error_message=Request exception!]",
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["heartbeat"]["ok"] is True
    assert "venue_heartbeat" in result["failing_surfaces"]
    assert result["surfaces"]["venue_heartbeat"]["issue"] == "VENUE_HEARTBEAT_LOST"


def test_loaded_sha_mismatch_yields_degraded_even_when_heartbeat_is_fresh(tmp_path: Path) -> None:
    """A fresh heartbeat from an old checkout is not current live authority."""

    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "loaded_sha.json",
        {"loaded_sha": "0" * 40, "generated_at": _now_iso(-10)},
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert "runtime_code" in result["failing_surfaces"]
    assert result["surfaces"]["runtime_code"]["ok"] is False
    assert result["surfaces"]["runtime_code"]["issue"].startswith("LOADED_SHA_MISMATCH")


def test_loaded_sha_invalid_shape_yields_degraded(tmp_path: Path) -> None:
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "loaded_sha.json",
        {"loaded_sha": "abc123", "generated_at": _now_iso(-10)},
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert result["surfaces"]["runtime_code"]["issue"] == "LOADED_SHA_INVALID:loaded=abc123"


def test_runtime_code_surface_degrades_dirty_runtime_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loaded SHA equality is not enough when runtime-plane files are dirty."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(
        live_health,
        "_dirty_runtime_worktree_paths",
        lambda **_kwargs: ("src/control/live_health.py", "src/execution/exit_lifecycle.py"),
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert "runtime_code" in result["failing_surfaces"]
    runtime_code = result["surfaces"]["runtime_code"]
    assert runtime_code["ok"] is False
    assert runtime_code["issue"] == "RUNTIME_WORKTREE_DIRTY"
    assert runtime_code["code_plane_status"] == "same_sha"
    assert runtime_code["worktree_runtime_dirty"] is True
    assert runtime_code["dirty_runtime_paths_sample"] == [
        "src/control/live_health.py",
        "src/execution/exit_lifecycle.py",
    ]


def test_process_code_started_before_runtime_source_mtime_yields_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh heartbeat cannot certify a daemon that predates live source files."""
    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    status_path = sd / "status_summary.json"
    status = json.loads(status_path.read_text())
    status["process"] = {
        "pid": 12345,
        "mode": "live",
        "version": "zeus_v2",
        "pulse_only": False,
    }
    _write(status_path, status)
    _write(
        sd / "daemon-heartbeat.json",
        {"alive": True, "timestamp": _now_iso(-30), "mode": "live", "pid": 12345},
    )
    monkeypatch.setattr(
        live_health,
        "_process_command_line",
        lambda _pid: "/Users/leofitz/zeus/.venv/bin/python -m src.main",
    )
    monkeypatch.setattr(live_health, "_process_start_epoch", lambda _pid: 1000.0)
    monkeypatch.setattr(
        live_health,
        "_latest_source_mtime",
        lambda _repo_root: (1010.0, "src/control/live_health.py"),
    )

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert "process_code" in result["failing_surfaces"]
    process_code = result["surfaces"]["process_code"]
    assert process_code["ok"] is False
    assert process_code["issue"] == "PROCESS_LOADED_CODE_STALE"
    assert process_code["pid"] == 12345
    assert process_code["source_path"] == "src/control/live_health.py"


def test_status_summary_dead_main_pid_yields_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale status_summary PID cannot certify the live daemon is still running."""

    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    status_path = sd / "status_summary.json"
    status = json.loads(status_path.read_text())
    status["process"] = {
        "pid": 999999,
        "mode": "live",
        "version": "zeus_v2",
        "pulse_only": False,
    }
    _write(status_path, status)
    monkeypatch.setattr(live_health, "_process_command_line", lambda pid: None)

    result = compute_composite_live_health(state_dir=sd)

    assert result["status"] == "DEGRADED"
    assert "main_daemon" in result["failing_surfaces"]
    assert result["surfaces"]["main_daemon"]["issue"] == "MAIN_DAEMON_PROCESS_NOT_FOUND"


def test_fresh_not_alive_heartbeat_yields_degraded(tmp_path: Path) -> None:
    """A fresh heartbeat with alive=false is a current failure, not healthy liveness."""

    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    _write(
        sd / "daemon-heartbeat.json",
        {
            "alive": False,
            "timestamp": _now_iso(-5),
            "mode": "live",
            "daemon_health": "BOOT_BLOCKED",
            "failure_reason": "LIVE_SIDECAR_BOOT_BLOCKED: sidecar stale",
        },
    )

    result = compute_composite_live_health(state_dir=sd)

    surface = result["surfaces"]["heartbeat"]
    assert surface["ok"] is False
    assert surface["issue"] == "NOT_ALIVE:LIVE_SIDECAR_BOOT_BLOCKED: sidecar stale"
    assert "heartbeat" in result["failing_surfaces"]


def test_live_trading_watchdog_loaded_false_ok_yields_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A loaded launchd label is not health when src.main is not running."""

    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    status_path = sd / "status_summary.json"
    status = json.loads(status_path.read_text())
    status["process"] = {
        "pid": 999999,
        "mode": "live",
        "version": "zeus_v2",
        "pulse_only": False,
    }
    _write(status_path, status)
    _write(
        sd / "live-trading-launchd-watchdog.json",
        {
            "ok": True,
            "action": "none",
            "reason": "service_loaded",
            "written_at": _now_iso(-5),
        },
    )
    monkeypatch.setattr(live_health, "_process_command_line", lambda pid: None)

    result = compute_composite_live_health(state_dir=sd)

    surface = result["surfaces"]["live_trading_watchdog"]
    assert surface["ok"] is False
    assert surface["issue"] == "LIVE_TRADING_WATCHDOG_FALSE_OK:service_loaded_not_running"
    assert surface["watchdog_reason"] == "service_loaded"
    assert surface["main_daemon_issue"] == "MAIN_DAEMON_PROCESS_NOT_FOUND"
    assert "live_trading_watchdog" in result["failing_surfaces"]


def test_live_boot_prerequisites_degrade_on_sidecar_sha_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A loaded launchd label is not enough when required sidecars run old code."""

    sd = tmp_path / "state"
    sd.mkdir()
    _setup_healthy_state(sd)
    monkeypatch.setattr(live_health, "_dirty_runtime_worktree_paths", lambda **_kwargs: ())
    heartbeat = json.loads((sd / "forecast-live-heartbeat.json").read_text())
    heartbeat["git_head"] = "2b436160d"
    _write(sd / "forecast-live-heartbeat.json", heartbeat)

    result = compute_composite_live_health(state_dir=sd)

    surface = result["surfaces"]["live_boot_prerequisites"]
    assert surface["ok"] is False
    assert surface["issue"] == "LIVE_BOOT_SIDECARS_NOT_READY:n=1"
    assert surface["failures"] == [
        f"forecast-live:git_head_mismatch heartbeat=2b436160d "
        f"expected={live_health._current_git_head()[:8]}"
    ]
    assert "live_boot_prerequisites" in result["failing_surfaces"]


def test_command_recovery_mutation_summary_requires_allocator_refresh() -> None:
    """Command recovery mutations must refresh live submit gating in-process."""
    from src.main import _command_recovery_summary_mutated_allocator_inputs

    assert not _command_recovery_summary_mutated_allocator_inputs(
        {"scanned": 1, "advanced": 0, "partial_remainders": {"advanced": 0}}
    )
    assert _command_recovery_summary_mutated_allocator_inputs(
        {"scanned": 1, "advanced": 1, "partial_remainders": {"advanced": 0}}
    )
    assert _command_recovery_summary_mutated_allocator_inputs(
        {"scanned": 1, "advanced": 0, "recorded_maker_fill_economics": {"projected": 17}}
    )


def test_edli_command_recovery_cycle_refreshes_allocator_after_mutation(monkeypatch) -> None:
    """The scheduled recovery job must refresh allocator state after DB mutations."""
    import src.execution.command_recovery as command_recovery
    import src.main as main_module
    import src.state.db as state_db

    class FakeConn:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake_conn = FakeConn()
    health_calls: list[tuple[str, bool, str | None]] = []
    refresh_calls: list[FakeConn] = []

    monkeypatch.setattr(main_module, "_settings_section", lambda name, default=None: {"enabled": True})
    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda job_name: False)
    monkeypatch.setattr(
        command_recovery,
        "reconcile_unresolved_commands",
        lambda **kwargs: health_calls.append(
            ("reconcile_scope", False, str(kwargs.get("scope")))
        ) or {"scanned": 1, "advanced": 1},
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection_with_world_required",
        lambda write_class=None: fake_conn,
    )
    monkeypatch.setattr(
        main_module,
        "_edli_refresh_global_allocator_for_live_bridge",
        lambda conn: refresh_calls.append(conn) or {"configured": True},
    )
    monkeypatch.setattr(
        main_module,
        "_write_scheduler_health",
        lambda job_name, failed=False, reason=None, **kwargs: health_calls.append(
            (job_name, failed, reason)
        ),
    )

    main_module._edli_command_recovery_cycle()

    assert refresh_calls == [fake_conn]
    assert fake_conn.closed is True
    assert ("reconcile_scope", False, "live_tick") in health_calls
    assert ("edli_command_recovery", False, None) in health_calls


def test_edli_command_recovery_runs_live_tick_during_active_redecision(monkeypatch) -> None:
    """Confirmed fill projection is part of the live management lane and must
    not starve behind continuous redecision activity."""
    import src.execution.command_recovery as command_recovery
    import src.main as main_module

    calls: list[str] = []

    monkeypatch.setattr(main_module, "_settings_section", lambda name, default=None: {"enabled": True})
    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda job_name: False)
    monkeypatch.setattr(main_module, "_edli_reactor_active", lambda: False)
    monkeypatch.setattr(
        main_module,
        "_edli_redecision_screen_lock",
        type("Locked", (), {"locked": lambda self: True})(),
    )
    monkeypatch.setattr(
        command_recovery,
        "reconcile_unresolved_commands",
        lambda **kwargs: calls.append(str(kwargs.get("scope"))) or {"scanned": 1, "advanced": 0},
    )

    main_module._edli_command_recovery_cycle()

    assert calls == ["live_tick"]


def test_edli_boot_command_recovery_runs_before_scheduler_tick(monkeypatch) -> None:
    """Boot must clear restart-relevant EDLI order locks before first reactor tick."""
    import src.execution.command_recovery as command_recovery
    import src.main as main_module
    import src.state.db as state_db

    class FakeConn:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake_conn = FakeConn()
    calls: list[str] = []
    refresh_calls: list[FakeConn] = []

    monkeypatch.setattr(main_module, "_settings_section", lambda name, default=None: {"enabled": True})
    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(
        command_recovery,
        "reconcile_unresolved_commands",
        lambda **kwargs: calls.append(str(kwargs.get("scope"))) or {"advanced": 1},
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection_with_world_required",
        lambda write_class=None: fake_conn,
    )
    monkeypatch.setattr(
        main_module,
        "_edli_refresh_global_allocator_for_live_bridge",
        lambda conn: refresh_calls.append(conn) or {"configured": True},
    )

    main_module._edli_boot_command_recovery_once()

    assert calls == ["boot_fast"]
    assert refresh_calls == [fake_conn]
    assert fake_conn.closed is True


def test_main_orders_boot_command_recovery_before_reactor_registration() -> None:
    """Boot-recoverable restart drift must be consumed before any entry reactor can submit."""
    import inspect
    import src.main as main_module

    source = inspect.getsource(main_module.main)

    boot_idx = source.index("_edli_boot_command_recovery_once()")
    reactor_idx = source.index("id=\"edli_event_reactor\"")
    start_idx = source.index("scheduler.start()")
    assert boot_idx < reactor_idx < start_idx


def test_boot_fast_command_recovery_includes_filled_entry_projection_repair() -> None:
    """Boot recovery must heal matched ENTRY fills before chain-sync sees them as chain-only."""
    import inspect
    import src.execution.command_recovery as command_recovery

    source = inspect.getsource(command_recovery._reconcile_passes_short_conn)
    boot_idx = source.index('if scope == "boot_fast":')
    live_idx = source.index('"live_entry_projection_repair"', boot_idx)
    filled_idx = source.index('"filled_entry_projection_repair"', boot_idx)
    hard_terminal_idx = source.index('"hard_terminal_position_projection_repair"', boot_idx)

    assert live_idx < filled_idx < hard_terminal_idx


def test_edli_command_recovery_emits_terminal_no_fill_continuation(monkeypatch) -> None:
    """A no-fill terminal order recovery must continue the redecision chain."""
    import src.execution.command_recovery as command_recovery
    import src.main as main_module
    import src.state.db as state_db

    class FakeConn:
        closed = False

        def close(self) -> None:
            self.closed = True

    trade_refresh_conn = FakeConn()
    trade_ro = FakeConn()
    forecasts_ro = FakeConn()
    summary = {
        "scanned": 1,
        "advanced": 1,
        "terminal_no_fill_continuations": [
            {"condition_id": "cond-1", "token_id": "tok-1", "command_id": "cmd-1"}
        ],
    }
    families = {("Singapore", "2026-06-27", "high")}
    emitted_calls: list[tuple[set[tuple[str, str, str]], str]] = []
    clear_calls: list[set[tuple[str, str, str]]] = []

    monkeypatch.setattr(
        main_module,
        "_settings_section",
        lambda name, default=None: {"enabled": True, "event_writer_enabled": True},
    )
    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda job_name: False)
    monkeypatch.setattr(
        command_recovery,
        "reconcile_unresolved_commands",
        lambda **kwargs: summary,
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection_with_world_required",
        lambda write_class=None: trade_refresh_conn,
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection_read_only",
        lambda: trade_ro,
    )
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_read_only",
        lambda: forecasts_ro,
    )
    monkeypatch.setattr(
        main_module,
        "_edli_refresh_global_allocator_for_live_bridge",
        lambda conn: {"configured": True},
    )
    monkeypatch.setattr(
        main_module,
        "_terminal_no_fill_continuation_families",
        lambda observed_summary, trade_conn, forecasts_conn: families,
    )
    monkeypatch.setattr(
        main_module,
        "_clear_redecision_acted_state_for_families",
        lambda observed_families: clear_calls.append(set(observed_families)) or 2,
    )
    monkeypatch.setattr(
        main_module,
        "_emit_terminal_no_fill_redecision_continuations",
        lambda observed_families, decision_time, received_at: (
            emitted_calls.append((set(observed_families), str(received_at))) or 1
        ),
    )

    main_module._edli_command_recovery_cycle()

    assert trade_refresh_conn.closed is True
    assert trade_ro.closed is True
    assert forecasts_ro.closed is True
    assert clear_calls == [families]
    assert emitted_calls and emitted_calls[0][0] == families


def test_terminal_no_fill_continuation_accepts_direct_family_identity() -> None:
    import src.main as main_module

    summary = {
        "terminal_no_fill_continuations": [
            {
                "city": "Boston",
                "target_date": "2026-06-23",
                "metric": "tmax",
                "condition_id": "cond-unused",
            }
        ]
    }

    assert main_module._terminal_no_fill_continuation_families(
        summary,
        trade_conn=object(),
        forecasts_conn=object(),
    ) == {("Boston", "2026-06-23", "high")}


def test_boot_auto_resolution_continuation_is_emitted_before_first_tick(monkeypatch) -> None:
    """Boot auto-resolution must not release a family and then leave it invisible."""
    import src.execution.command_recovery as command_recovery
    import src.execution.edli_absence_resolver as resolver_mod
    import src.main as main_module
    import src.state.db as state_db

    class FakeConn:
        closed = False

        def close(self) -> None:
            self.closed = True

    trade_ro = FakeConn()
    forecasts_ro = FakeConn()
    families = {("Hong Kong", "2026-06-19", "low")}
    emitted_calls: list[set[tuple[str, str, str]]] = []
    clear_calls: list[set[tuple[str, str, str]]] = []

    monkeypatch.setattr(
        main_module,
        "_settings_section",
        lambda name, default=None: {"enabled": True, "event_writer_enabled": True},
    )
    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(
        command_recovery,
        "reconcile_unresolved_commands",
        lambda **kwargs: {"scanned": 0, "advanced": 0},
    )
    monkeypatch.setattr(
        resolver_mod,
        "take_boot_auto_resolution_continuations",
        lambda: [
            {
                "city": "Hong Kong",
                "target_date": "2026-06-19",
                "metric": "tmin",
            }
        ],
    )
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: trade_ro)
    monkeypatch.setattr(state_db, "get_forecasts_connection_read_only", lambda: forecasts_ro)
    monkeypatch.setattr(
        main_module,
        "_clear_redecision_acted_state_for_families",
        lambda observed_families: clear_calls.append(set(observed_families)) or 1,
    )
    monkeypatch.setattr(
        main_module,
        "_emit_terminal_no_fill_redecision_continuations",
        lambda observed_families, decision_time, received_at: (
            emitted_calls.append(set(observed_families)) or 1
        ),
    )

    main_module._edli_boot_command_recovery_once()

    assert trade_ro.closed is True
    assert forecasts_ro.closed is True
    assert clear_calls == [families]
    assert emitted_calls == [families]


def test_terminal_no_fill_redecision_counts_write_many_results(monkeypatch) -> None:
    """EventWriter.write_many returns EventWriteResult rows, not an int.

    The boot/periodic no-fill continuation bridge must count inserted results
    instead of raising TypeError("int() ... not 'list'"), otherwise cancel/no-fill
    recovery releases a family lock but fails to requeue the family for
    redecision.
    """
    from types import SimpleNamespace

    import src.events.event_writer as event_writer_mod
    import src.events.triggers.forecast_snapshot_ready as trigger_mod
    import src.main as main_module
    import src.state.db as state_db

    class FakeConn:
        committed = False
        closed = False

        def commit(self) -> None:
            self.committed = True

        def close(self) -> None:
            self.closed = True

    class FakeMutex:
        acquired = False
        released = False

        def acquire(self) -> None:
            self.acquired = True

        def release(self) -> None:
            self.released = True

    world = FakeConn()
    forecasts = FakeConn()
    mutex = FakeMutex()

    class FakeWriter:
        def __init__(self, conn):
            self.conn = conn

        def write_many(self, events):
            assert len(events) == 2
            return [
                SimpleNamespace(inserted=True),
                SimpleNamespace(inserted=False),
            ]

    class FakeTrigger:
        def __init__(self, writer, *, live_eligibility_reader):
            self.writer = writer
            self.live_eligibility_reader = live_eligibility_reader

        def build_committed_snapshot_events(self, **kwargs):
            assert kwargs["restrict_to_families"] == {("Paris", "2026-06-20", "low")}
            return [object(), object()]

    monkeypatch.setattr(state_db, "get_world_connection", lambda: world)
    monkeypatch.setattr(state_db, "get_forecasts_connection_read_only", lambda: forecasts)
    monkeypatch.setattr(state_db, "world_write_mutex", lambda: mutex)
    monkeypatch.setattr(
        trigger_mod,
        "executable_forecast_live_eligible_reader",
        lambda conn: "reader",
    )
    monkeypatch.setattr(trigger_mod, "ForecastSnapshotReadyTrigger", FakeTrigger)
    monkeypatch.setattr(event_writer_mod, "EventWriter", FakeWriter)
    monkeypatch.setattr(main_module, "_redecision_event_with_origin", lambda event, origin: event)

    emitted = main_module._emit_terminal_no_fill_redecision_continuations(
        {("Paris", "2026-06-20", "low")},
        decision_time=main_module.datetime.now(main_module.timezone.utc),
        received_at=main_module.datetime.now(main_module.timezone.utc).isoformat(),
    )

    assert emitted == 1
    assert world.committed is True
    assert world.closed is True
    assert forecasts.closed is True
    assert mutex.acquired is True
    assert mutex.released is True
