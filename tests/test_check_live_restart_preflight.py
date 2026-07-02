# Lifecycle: created=2026-06-18; last_reviewed=2026-06-28; last_reused=2026-07-02
# Purpose: Regression tests for read-only live restart preflight risk classification.
# Reuse: pytest tests/test_check_live_restart_preflight.py
# Authority basis: AGENTS.md live-money restart proof gates.

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from scripts import check_live_restart_preflight as preflight
from src.decision import qlcb_reliability_guard as guard_mod
from src.state.decision_integrity_quarantine import (
    DECISION_CERTIFICATES_TABLE,
    REASON_INVALID_LIVE_ACTIONABLE,
)


def _qlcb_meta() -> dict[str, object]:
    return {
        "schema_version": guard_mod.EXPECTED_SCHEMA_VERSION,
        "guard_semantic_version": guard_mod.EXPECTED_GUARD_SEMANTIC_VERSION,
        "center_method_version": guard_mod.EXPECTED_CENTER_METHOD_VERSION,
        "band_semantic_version": guard_mod.EXPECTED_BAND_SEMANTIC_VERSION,
        "corpus_authority": guard_mod.EXPECTED_CORPUS_AUTHORITY,
    }


def _init_trade_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            bin_label TEXT,
            direction TEXT,
            shares REAL,
            chain_shares REAL,
            order_status TEXT,
            exit_reason TEXT,
            exit_retry_count INTEGER,
            next_exit_retry_at TEXT,
            last_monitor_prob REAL,
            last_monitor_prob_is_fresh INTEGER,
            last_monitor_market_price REAL,
            last_monitor_market_price_is_fresh INTEGER,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def _insert_monitor_events(
    conn: sqlite3.Connection,
    *,
    position_id: str = "pos-1",
    monitor_at: datetime,
    chain_at: datetime | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    payload_json = json.dumps(payload or {})
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at, payload_json
        ) VALUES (?, ?, 1, 'MONITOR_REFRESHED', ?, ?)
        """,
        (f"evt-monitor-{position_id}", position_id, monitor_at.isoformat(), payload_json),
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at, payload_json
        ) VALUES (?, ?, 2, 'CHAIN_SIZE_CORRECTED', ?, '{}')
        """,
        (f"evt-chain-{position_id}", position_id, (chain_at or now).isoformat()),
    )
    conn.commit()


def _insert_open_position_with_monitor_events(
    conn: sqlite3.Connection,
    *,
    monitor_at: datetime,
    chain_at: datetime | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            bin_label, direction, shares, chain_shares, order_status,
            exit_reason, exit_retry_count, next_exit_retry_at,
            last_monitor_prob, last_monitor_prob_is_fresh,
            last_monitor_market_price, last_monitor_market_price_is_fresh,
            updated_at
        ) VALUES (
            'pos-1', 'active', 'Kuala Lumpur', '2026-07-02', 'high',
            'Will the highest temperature in Kuala Lumpur be 34°C on July 2?',
            'buy_yes', 10.0, 10.0, 'partial', NULL, 0, NULL,
            0.12, 1, 0.03, 1, ?
        )
        """,
        (now.isoformat(),),
    )
    _insert_monitor_events(conn, monitor_at=monitor_at, chain_at=chain_at)


def _init_forecast_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            q_json TEXT NOT NULL,
            runtime_layer TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT,
            target_date TEXT,
            market_slug TEXT,
            winning_bin TEXT,
            temperature_metric TEXT,
            authority TEXT,
            settlement_source TEXT,
            settlement_value REAL,
            settled_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def _live_entry_submit_payload() -> dict[str, object]:
    return {
        "execution_capability": {
            "allowed": True,
            "components": [
                {"component": "cutover_guard", "allowed": True, "reason": "allowed"},
                {
                    "component": "entry_economics",
                    "allowed": True,
                    "reason": "allowed",
                    "details": {
                        "q_live": 0.82,
                        "q_lcb_5pct": 0.72,
                        "expected_edge": 0.70,
                        "limit_price": 0.50,
                        "submit_edge": 0.22,
                        "expected_profit_usd": 4.40,
                        "min_entry_price": 0.05,
                        "min_expected_profit_usd": 1.0,
                        "submit_edge_density": 0.44,
                        "min_submit_edge_density": 0.05,
                        "shares": 20.0,
                        "qkernel_side": "YES",
                    },
                },
                {
                    "component": "entry_actionable_certificate",
                    "allowed": True,
                    "reason": "allowed",
                    "details": {
                        "certificate_hash": "a" * 64,
                        "certificate_schema": "main",
                    },
                },
            ],
        }
    }


def _init_actionable_world_db(
    path,
    payload: dict,
    *,
    decision_time: datetime | None = None,
    certificate_hash: str = "hash-test",
):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE decision_certificates (
            certificate_id TEXT PRIMARY KEY,
            certificate_hash TEXT NOT NULL,
            certificate_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            verifier_status TEXT NOT NULL,
            decision_time TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    now = decision_time or datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO decision_certificates (
            certificate_id, certificate_hash, certificate_type, mode,
            verifier_status, decision_time, payload_json
        ) VALUES (?, ?, 'ActionableTradeCertificate', 'LIVE', 'VERIFIED', ?, ?)
        """,
        (
            "ActionableTradeCertificate:test",
            certificate_hash,
            now.isoformat(),
            json.dumps(payload),
        ),
    )
    conn.commit()
    conn.close()


def _init_entry_command_trade_db(
    path,
    *,
    event_id: str,
    token_id: str = "yes-1",
    state: str = "ACKED",
):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            envelope_id TEXT NOT NULL,
            position_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            intent_kind TEXT NOT NULL,
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            price REAL NOT NULL,
            venue_order_id TEXT,
            state TEXT NOT NULL,
            last_event_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            review_required_reason TEXT
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, 'snap-1', 'env-1', 'pos-1', ?, 'idem-1', 'ENTRY',
                  'market-1', ?, 'BUY', 1.0, 0.4, 'venue-1', ?, NULL, ?, ?, NULL)
        """,
        ("cmd-1", f"edli_exec_cmd:{event_id}", token_id, state, now, now),
    )
    conn.commit()
    conn.close()


def _attach_forecast_parent(
    world_db,
    *,
    child_certificate_id: str = "ActionableTradeCertificate:test",
    parent_hash: str = "forecast-parent-hash",
    posterior_identity_hash: str = "posterior-hash",
):
    conn = sqlite3.connect(world_db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_certificate_edges (
            child_certificate_id TEXT NOT NULL,
            parent_role TEXT NOT NULL,
            parent_certificate_hash TEXT NOT NULL,
            parent_certificate_type TEXT NOT NULL,
            required INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO decision_certificates (
            certificate_id, certificate_hash, certificate_type, mode,
            verifier_status, decision_time, payload_json
        ) VALUES (?, ?, 'ForecastAuthorityCertificate', 'LIVE', 'VERIFIED', ?, ?)
        """,
        (
            "ForecastAuthorityCertificate:test",
            parent_hash,
            datetime.now(timezone.utc).isoformat(),
            json.dumps({"posterior_identity_hash": posterior_identity_hash}),
        ),
    )
    conn.execute(
        """
        INSERT INTO decision_certificate_edges VALUES (
            ?, 'forecast_authority', ?, 'ForecastAuthorityCertificate', 1, ?
        )
        """,
        (child_certificate_id, parent_hash, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def _init_parent_posterior_forecast_db(
    path,
    *,
    posterior_identity_hash: str = "posterior-hash",
    bin_label: str = "Will the highest temperature in Istanbul be 29°C on June 29?",
    q_yes: float = 0.3462,
    q_lcb_yes: float = 0.1124,
    q_ucb_yes: float = 0.3826,
):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_identity_hash TEXT PRIMARY KEY,
            q_json TEXT NOT NULL,
            q_lcb_json TEXT NOT NULL,
            q_ucb_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_identity_hash, q_json, q_lcb_json, q_ucb_json
        ) VALUES (?, ?, ?, ?)
        """,
        (
            posterior_identity_hash,
            json.dumps({bin_label: q_yes}),
            json.dumps({bin_label: q_lcb_yes}),
            json.dumps({bin_label: q_ucb_yes}),
        ),
    )
    conn.commit()
    conn.close()


def _valid_actionable_payload() -> dict:
    return {
        "event_id": "event-1",
        "event_type": "FORECAST_SNAPSHOT_READY",
        "causal_snapshot_id": "snap-1",
        "family_id": "family-1",
        "candidate_id": "candidate-1",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "direction": "buy_yes",
        "strategy_key": "center_buy",
        "executable_snapshot_id": "exec-1",
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "c_fee_adjusted": 0.4,
        "c_cost_95pct": 0.45,
        "p_fill_lcb": 0.1,
        "trade_score": 0.2,
        "action_score": 0.2,
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.7,
            "payoff_q_lcb": 0.6,
            "cost": 0.4,
            "edge_lcb": 0.2,
            "optimal_delta_u": 0.01,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 3.0,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.6,
            "selection_guard_cell_key": "high|L2_3|YES|nonmodal|pb17",
            "coherence_allows": True,
        },
        "fdr_family_id": "family-1",
        "kelly_decision_id": "kelly-1",
        "kelly_size_usd": 3.0,
        "risk_decision_id": "risk-1",
        "live_cap_usage_id": "cap-1",
        "final_intent_id": "intent-1",
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "native_quote_available": True,
        "submitted": False,
    }


def _init_live_order_world_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE edli_live_order_events (
            aggregate_event_id TEXT PRIMARY KEY,
            aggregate_id TEXT NOT NULL,
            event_sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            parent_event_hash TEXT,
            event_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            source_authority TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE edli_live_order_projection (
            aggregate_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            final_intent_id TEXT,
            current_state TEXT NOT NULL,
            last_sequence INTEGER NOT NULL,
            last_event_type TEXT,
            last_event_hash TEXT,
            pending_reconcile INTEGER NOT NULL,
            venue_order_id TEXT,
            updated_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _patch_paths(monkeypatch, tmp_path):
    trade_db = tmp_path / "zeus_trades.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    world_db = tmp_path / "zeus-world.db"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings = tmp_path / "settings.json"
    scheduler_health = tmp_path / "scheduler_jobs_health.json"
    forecast_live_heartbeat = tmp_path / "forecast-live-heartbeat.json"
    live_plist = tmp_path / "com.zeus.live-trading.plist"
    qlcb_artifact = state_dir / "qlcb_oof_reliability.json"
    now = datetime.now(timezone.utc).isoformat()
    settings.write_text(
        json.dumps(
            {
                "edli": {
                    "live_execution_mode": "edli_live",
                    "reactor_mode": "live",
                    "real_order_submit_enabled": True,
                },
                "feature_flags": {"qkernel_spine_enabled": True},
            }
        )
    )
    live_plist.write_bytes(
        (
            b"""<?xml version="1.0" encoding="UTF-8"?>\n"""
            b"""<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" """
            b""""http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n"""
            b"""<plist version="1.0"><dict>"""
            b"""<key>Label</key><string>com.zeus.live-trading</string>"""
            b"""<key>ProgramArguments</key><array>"""
            b"""<string>/usr/bin/python3</string><string>-m</string><string>src.main</string>"""
            b"""</array>"""
            b"""<key>WorkingDirectory</key><string>"""
            + str(preflight.ROOT).encode()
            + b"""</string>"""
            b"""<key>EnvironmentVariables</key><dict>"""
            b"""<key>ZEUS_HARVESTER_LIVE_ENABLED</key><string>1</string>"""
            b"""<key>POLYMARKET_CLOB_V2_SIGNATURE_TYPE</key><string>2</string>"""
            b"""</dict></dict></plist>\n"""
        )
    )
    scheduler_health.write_text(
        json.dumps(
            {
                "bayes_precision_fusion_capture": {
                    "status": "OK",
                    "last_run_at": now,
                    "last_success_at": now,
                },
                "replacement_forecast_download": {
                    "status": "OK",
                    "last_run_at": now,
                    "last_success_at": now,
                },
                "replacement_forecast_live_materialize": {
                    "status": "OK",
                    "last_run_at": now,
                    "last_success_at": now,
                },
            }
        )
    )
    forecast_live_heartbeat.write_text(
        json.dumps(
            {
                "daemon": "forecast-live",
                "status": "alive",
                "timestamp": now,
                "written_at": now,
                "pid": 123,
                "git_head": "testsha",
                "jobs": [
                    "forecast_live_heartbeat",
                    "replacement_forecast_download",
                    "replacement_forecast_live_materialize",
                ],
            }
        )
    )
    qlcb_artifact.write_text(
        json.dumps(
            {
                "meta": _qlcb_meta(),
                "cells": {
                    "high|L1|YES|modal|qb1|coarse_global": {"n": 100, "hit_rate": 0.80},
                    "high|L1|NO|nonmodal|qb1|coarse_global": {"n": 100, "hit_rate": 0.80},
                }
            }
        )
    )
    sqlite3.connect(world_db).close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "SETTINGS_PATH", settings)
    monkeypatch.setattr(preflight, "STATE_DIR", state_dir)
    monkeypatch.setattr(preflight, "SCHEDULER_HEALTH_PATH", scheduler_health)
    monkeypatch.setattr(preflight, "FORECAST_LIVE_HEARTBEAT_PATH", forecast_live_heartbeat)
    monkeypatch.setattr(preflight, "LIVE_TRADING_PLIST_PATH", live_plist)
    monkeypatch.setattr(
        preflight,
        "_src_main_boot_guard_check",
        lambda: preflight.CheckResult(
            "src_main_boot_guards",
            True,
            "src.main boot guards pass",
            {"test_stub": True},
        ),
    )
    monkeypatch.setattr(
        guard_mod,
        "_QLCB_OOF_RELIABILITY_PATH",
        str(state_dir / "qlcb_oof_reliability.json"),
    )
    guard_mod.reset_reliability_cache()
    monkeypatch.delenv("ZEUS_HARVESTER_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("ZEUS_LIVE_FAMILY_PORTFOLIO_MAX_LEGS", raising=False)
    monkeypatch.setattr(preflight, "_live_main_processes", lambda: [])
    monkeypatch.setattr(
        preflight,
        "_live_trading_launchagent_bootstrapable_check",
        lambda: preflight.CheckResult(
            "live_trading_launchagent_bootstrapable",
            True,
            "active live-trading LaunchAgent is enabled for restart",
            {"test_stub": True},
        ),
    )
    monkeypatch.setattr(preflight, "_git_head", lambda: "testsha")
    return trade_db, forecast_db, state_dir


def test_src_main_boot_guard_check_blocks_failed_validate_boot(monkeypatch):
    calls = []
    monkeypatch.setattr(preflight, "_live_trading_python_executable", lambda: "/tmp/live-python")

    def _fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            1,
            "FAIL frozen_as_of_staleness: FROZEN_AS_OF_STALE\n",
            "",
        )

    monkeypatch.setattr(preflight.subprocess, "run", _fake_run)

    result = preflight._src_main_boot_guard_check()

    assert result.ok is False
    assert "restart would crash" in result.detail
    assert result.evidence["returncode"] == 1
    assert "FAIL frozen_as_of_staleness" in result.evidence["stdout_tail"]
    assert calls
    assert calls[0][0][0] == "/tmp/live-python"
    assert result.evidence["command"][0] == "/tmp/live-python"


def test_src_main_boot_guard_check_passes_validate_boot(monkeypatch):
    monkeypatch.setattr(preflight, "_live_trading_python_executable", lambda: "/tmp/live-python")

    def _fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "zeus --validate-boot: ALL PASS (exit 0)\n",
            "",
        )

    monkeypatch.setattr(preflight.subprocess, "run", _fake_run)

    result = preflight._src_main_boot_guard_check()

    assert result.ok is True
    assert result.evidence["returncode"] == 0
    assert result.evidence["command"][0] == "/tmp/live-python"


def test_src_main_boot_guard_uses_launchagent_python(monkeypatch, tmp_path):
    plist = tmp_path / "com.zeus.live-trading.plist"
    plist.write_bytes(
        b"""<?xml version="1.0" encoding="UTF-8"?>\n"""
        b"""<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" """
        b""""http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n"""
        b"""<plist version="1.0"><dict>"""
        b"""<key>Label</key><string>com.zeus.live-trading</string>"""
        b"""<key>ProgramArguments</key><array>"""
        b"""<string>/tmp/live-venv-python</string><string>-m</string><string>src.main</string>"""
        b"""</array>"""
        b"""<key>EnvironmentVariables</key><dict>"""
        b"""<key>SECRET_SHOULD_NOT_BE_READ</key><string>redacted</string>"""
        b"""</dict></dict></plist>\n"""
    )
    calls = []

    def _fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(preflight, "LIVE_TRADING_PLIST_PATH", plist)
    monkeypatch.setattr(preflight.subprocess, "run", _fake_run)

    result = preflight._src_main_boot_guard_check()

    assert result.ok is True
    assert calls[0][0][0] == "/tmp/live-venv-python"
    assert result.evidence["command"][0] == "/tmp/live-venv-python"
    assert "SECRET_SHOULD_NOT_BE_READ" not in json.dumps(result.evidence)


def test_posterior_summary_uses_source_cycle_freshness_not_computed_age(
    monkeypatch, tmp_path
):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    _init_trade_db(trade_db).close()
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Lucknow', '2026-06-28', 'high', ?, ?, '{}', 'live')
        """,
        (
            (now - timedelta(hours=14)).isoformat(),
            (now - timedelta(hours=6)).isoformat(),
        ),
    )
    forecasts.commit()
    forecasts.close()

    result = preflight._posterior_summary()

    assert result.ok is True
    assert result.evidence["freshness_basis"] == "source_cycle_time"
    assert result.evidence["latest_live_age_hours"] > 3.0
    assert result.evidence["latest_live_source_cycle_age_hours"] < 30.0


def test_posterior_summary_blocks_expired_source_cycle(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    _init_trade_db(trade_db).close()
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Lucknow', '2026-06-28', 'high', ?, ?, '{}', 'live')
        """,
        (
            (now - timedelta(hours=36)).isoformat(),
            (now - timedelta(hours=1)).isoformat(),
        ),
    )
    forecasts.commit()
    forecasts.close()

    result = preflight._posterior_summary()

    assert result.ok is False
    assert result.evidence["freshness_basis"] == "source_cycle_time"
    assert result.evidence["latest_live_source_cycle_age_hours"] > 30.0


def test_live_input_posterior_cycle_alignment_blocks_newer_raw_cycle(
    monkeypatch, tmp_path
):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    _init_trade_db(trade_db).close()
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    target_date = now.date().isoformat()
    posterior_cycle = (now - timedelta(hours=12)).replace(microsecond=0)
    raw_cycle = (now - timedelta(hours=6)).replace(microsecond=0)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (
            1, 'Buenos Aires', ?, 'high', ?, ?, '{}', 'live'
        )
        """,
        (target_date, posterior_cycle.isoformat(), (now - timedelta(hours=1)).isoformat()),
    )
    forecasts.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            endpoint TEXT,
            coverage_status TEXT,
            captured_at TEXT,
            source_available_at TEXT,
            source_id TEXT,
            product_id TEXT
        )
        """
    )
    for model, source_id, product_id in (
        ("ecmwf_ifs", "ecmwf_ifs_single_runs", "ecmwf_ifs::single_runs"),
        ("icon_global", "icon_global_single_runs", "icon_global::single_runs"),
    ):
        forecasts.execute(
            """
            INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time, endpoint,
                coverage_status, captured_at, source_available_at, source_id, product_id
            ) VALUES (?, 'Buenos Aires', ?, 'high', ?,
                      'single_runs', 'COVERED', ?, ?, ?, ?)
            """,
            (
                model,
                target_date,
                raw_cycle.isoformat(),
                (raw_cycle + timedelta(minutes=10)).isoformat(),
                (raw_cycle + timedelta(minutes=5)).isoformat(),
                source_id,
                product_id,
            ),
        )
    forecasts.execute(
        """
        CREATE TABLE market_events (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            token_id TEXT,
            range_label TEXT
        )
        """
    )
    forecasts.execute(
        """
        INSERT INTO market_events VALUES (
            'Buenos Aires', ?, 'high', 'token-yes', '11°C'
        )
        """,
        (target_date,),
    )
    forecasts.commit()
    forecasts.close()

    result = preflight._live_input_posterior_cycle_alignment_check()

    assert result.ok is False
    assert result.evidence["lagged_or_missing_count"] == 1
    assert result.evidence["samples"][0]["city"] == "Buenos Aires"
    assert result.evidence["samples"][0]["risk"] == "live_posterior_cycle_lag"
    assert result.evidence["samples"][0]["raw_cycle"] == raw_cycle.isoformat()
    assert result.evidence["samples"][0]["posterior_cycle"] == posterior_cycle.isoformat()


def test_live_input_posterior_cycle_alignment_ignores_closed_old_targets(
    monkeypatch, tmp_path
):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    _init_trade_db(trade_db).close()
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    old_target_date = (now.date() - timedelta(days=2)).isoformat()
    posterior_cycle = (now - timedelta(hours=36)).replace(microsecond=0)
    raw_cycle = (now - timedelta(hours=30)).replace(microsecond=0)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (
            1, 'Buenos Aires', ?, 'high', ?, ?, '{}', 'live'
        )
        """,
        (old_target_date, posterior_cycle.isoformat(), (now - timedelta(hours=1)).isoformat()),
    )
    forecasts.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            endpoint TEXT,
            coverage_status TEXT,
            captured_at TEXT,
            source_available_at TEXT,
            source_id TEXT,
            product_id TEXT
        )
        """
    )
    for model, source_id, product_id in (
        ("ecmwf_ifs", "ecmwf_ifs_single_runs", "ecmwf_ifs::single_runs"),
        ("icon_global", "icon_global_single_runs", "icon_global::single_runs"),
    ):
        forecasts.execute(
            """
            INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time, endpoint,
                coverage_status, captured_at, source_available_at, source_id, product_id
            ) VALUES (?, 'Buenos Aires', ?, 'high', ?,
                      'single_runs', 'COVERED', ?, ?, ?, ?)
            """,
            (
                model,
                old_target_date,
                raw_cycle.isoformat(),
                (raw_cycle + timedelta(minutes=10)).isoformat(),
                (raw_cycle + timedelta(minutes=5)).isoformat(),
                source_id,
                product_id,
            ),
        )
    forecasts.execute(
        """
        CREATE TABLE market_events (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            token_id TEXT,
            range_label TEXT
        )
        """
    )
    forecasts.execute(
        """
        INSERT INTO market_events VALUES (
            'Buenos Aires', ?, 'high', 'token-yes', '11°C'
        )
        """,
        (old_target_date,),
    )
    forecasts.commit()
    forecasts.close()

    result = preflight._live_input_posterior_cycle_alignment_check()

    assert result.ok is True
    assert result.evidence["lagged_or_missing_count"] == 0
    assert result.evidence["active_target_floor_date"] == now.date().isoformat()


def test_live_actionable_certificate_semantics_audits_unreferenced_qkernel_mismatch(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    payload = {
        **_valid_actionable_payload(),
        "city": "Lucknow",
        "target_date": "2026-06-28",
        "temperature_metric": "high",
        "bin_label": "Will the highest temperature in Lucknow be 35°C or below on June 28?",
        "q_live": 0.005426579861923467,
        "q_lcb_5pct": 0.003,
        "c_fee_adjusted": 0.014885316546202029,
        "c_cost_95pct": 0.011,
        "p_fill_lcb": 0.9997671696598043,
        "trade_score": 0.04049776073684555,
        "action_score": 0.04049776073684555,
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.22351072116676574,
            "payoff_q_lcb": 0.05049776073684555,
            "cost": 0.01,
            "edge_lcb": 0.04049776073684555,
            "optimal_delta_u": 0.013993788651471595,
            "delta_u_at_min": 0.013993788651471595,
            "optimal_stake_usd": 4.0,
            "false_edge_rate": 0.02599350162459385,
            "direction_law_ok": False,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.05049776073684555,
            "selection_guard_cell_key": "high|L2_3|YES|nonmodal|pb17",
            "coherence_allows": True,
        },
    }
    _init_actionable_world_db(world_db, payload)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)

    result = preflight._live_actionable_certificate_semantics_check()

    assert result.ok is True
    assert result.evidence["risky_count"] == 0
    assert result.evidence["historical_risky_count"] == 1
    assert result.evidence["historical_risky"][0]["city"] == "Lucknow"
    assert "payoff_q_point mismatches" in result.evidence["historical_risky"][0]["reason"]


def test_live_actionable_certificate_semantics_blocks_referenced_qkernel_mismatch(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    trade_db = tmp_path / "zeus_trades.db"
    payload = {
        **_valid_actionable_payload(),
        "event_id": "event-1",
        "token_id": "yes-1",
        "city": "Lucknow",
        "target_date": "2026-06-28",
        "temperature_metric": "high",
        "bin_label": "Will the highest temperature in Lucknow be 35°C or below on June 28?",
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.7,
            "payoff_q_lcb": 0.6,
            "cost": 0.4,
            "edge_lcb": 0.2,
            "optimal_delta_u": 0.01,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 3.0,
            "false_edge_rate": 0.01,
            "direction_law_ok": False,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.6,
            "selection_guard_cell_key": "high|L2_3|YES|nonmodal|pb17",
            "q_lcb_guard_basis": "OOF_WILSON_95",
            "q_lcb_guard_abstained": False,
            "q_lcb_guard_cell_key": "high|L2_3|YES|nonmodal|qb2|coarse_global",
            "coherence_allows": True,
        },
    }
    _init_actionable_world_db(world_db, payload)
    _init_entry_command_trade_db(trade_db, event_id="event-1", token_id="yes-1")
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)

    result = preflight._live_actionable_certificate_semantics_check()

    assert result.ok is False
    assert result.evidence["risky_count"] == 1
    assert result.evidence["historical_risky_count"] == 0
    assert result.evidence["restart_relevant_entry_command_count"] == 1
    assert result.evidence["risky"][0]["city"] == "Lucknow"
    assert "qkernel direction admission" in result.evidence["risky"][0]["reason"]


def test_live_actionable_certificate_semantics_accepts_current_qkernel_payload(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    _init_actionable_world_db(world_db, _valid_actionable_payload())
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)

    result = preflight._live_actionable_certificate_semantics_check()

    assert result.ok is True
    assert result.evidence["checked_count"] == 1
    assert result.evidence["risky_count"] == 0


def test_live_actionable_certificate_semantics_audits_qkernel_above_forecast_parent(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    bin_label = "Will the highest temperature in Istanbul be 29°C on June 29?"
    payload = {
        **_valid_actionable_payload(),
        "city": "Istanbul",
        "target_date": "2026-06-29",
        "temperature_metric": "high",
        "bin_label": bin_label,
        "direction": "buy_no",
        "q_live": 0.8768631118304586,
        "q_lcb_5pct": 0.8198679378026374,
        "c_fee_adjusted": 0.56,
        "trade_score": 0.2598679378026374,
        "action_score": 0.2598679378026374,
        "qkernel_execution_economics": {
            **_valid_actionable_payload()["qkernel_execution_economics"],
            "side": "NO",
            "payoff_q_point": 0.8768631118304586,
            "payoff_q_lcb": 0.8198679378026374,
            "cost": 0.56,
            "edge_lcb": 0.2598679378026374,
            "selection_guard_q_safe": 0.8198679378026374,
        },
    }
    _init_actionable_world_db(world_db, payload)
    _attach_forecast_parent(world_db)
    _init_parent_posterior_forecast_db(forecast_db, bin_label=bin_label)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._live_actionable_certificate_semantics_check()

    assert result.ok is True
    assert result.evidence["risky_count"] == 0
    assert result.evidence["historical_risky_count"] == 1
    assert "exceeds forecast parent posterior" in result.evidence["historical_risky"][0]["reason"]


def test_live_actionable_certificate_semantics_blocks_restart_relevant_qkernel_parent_drift(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    trade_db = tmp_path / "zeus_trades.db"
    bin_label = "Will the highest temperature in Istanbul be 29°C on June 29?"
    payload = {
        **_valid_actionable_payload(),
        "city": "Istanbul",
        "target_date": "2026-06-29",
        "temperature_metric": "high",
        "bin_label": bin_label,
        "direction": "buy_no",
        "token_id": "no-1",
        "q_live": 0.8768631118304586,
        "q_lcb_5pct": 0.8198679378026374,
        "c_fee_adjusted": 0.56,
        "trade_score": 0.2598679378026374,
        "action_score": 0.2598679378026374,
        "qkernel_execution_economics": {
            **_valid_actionable_payload()["qkernel_execution_economics"],
            "side": "NO",
            "payoff_q_point": 0.8768631118304586,
            "payoff_q_lcb": 0.8198679378026374,
            "cost": 0.56,
            "edge_lcb": 0.2598679378026374,
            "selection_guard_q_safe": 0.8198679378026374,
        },
    }
    _init_actionable_world_db(world_db, payload)
    _attach_forecast_parent(world_db)
    _init_parent_posterior_forecast_db(forecast_db, bin_label=bin_label)
    _init_entry_command_trade_db(trade_db, event_id="event-1", token_id="no-1")
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)

    result = preflight._live_actionable_certificate_semantics_check()

    assert result.ok is False
    assert result.evidence["risky_count"] == 1
    assert result.evidence["historical_risky_count"] == 0
    assert "exceeds forecast parent posterior" in result.evidence["risky"][0]["reason"]


def test_live_actionable_certificate_semantics_allows_boot_auto_cancelable_invalid_pending_entry(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    trade_db = tmp_path / "zeus_trades.db"
    bin_label = "Will the highest temperature in Istanbul be 29°C on June 29?"
    payload = {
        **_valid_actionable_payload(),
        "event_id": "event-1",
        "city": "Istanbul",
        "target_date": "2026-06-29",
        "temperature_metric": "high",
        "bin_label": bin_label,
        "direction": "buy_no",
        "token_id": "no-1",
        "q_live": 0.8768631118304586,
        "q_lcb_5pct": 0.8198679378026374,
        "c_fee_adjusted": 0.56,
        "trade_score": 0.2598679378026374,
        "action_score": 0.2598679378026374,
        "qkernel_execution_economics": {
            **_valid_actionable_payload()["qkernel_execution_economics"],
            "side": "NO",
            "payoff_q_point": 0.8768631118304586,
            "payoff_q_lcb": 0.8198679378026374,
            "cost": 0.56,
            "edge_lcb": 0.2598679378026374,
            "selection_guard_q_safe": 0.8198679378026374,
        },
    }
    _init_actionable_world_db(world_db, payload)
    _attach_forecast_parent(world_db)
    _init_parent_posterior_forecast_db(forecast_db, bin_label=bin_label)
    _init_entry_command_trade_db(trade_db, event_id="event-1", token_id="no-1")
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            cost_basis_usd REAL,
            chain_shares REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, cost_basis_usd, chain_shares
        ) VALUES ('pos-1', 'pending_entry', 0.0, 0.0, 0.0)
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            fact_id INTEGER PRIMARY KEY,
            command_id TEXT,
            venue_order_id TEXT,
            state TEXT,
            matched_size TEXT,
            remaining_size TEXT,
            local_sequence INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_order_facts (
            fact_id, command_id, venue_order_id, state,
            matched_size, remaining_size, local_sequence
        ) VALUES (1, 'cmd-1', 'venue-1', 'LIVE', '0', '1.0', 1)
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)

    result = preflight._live_actionable_certificate_semantics_check()

    assert result.ok is True
    assert result.evidence["risky_count"] == 0
    assert result.evidence["historical_risky_count"] == 0
    assert result.evidence["auto_recoverable_invalid_pending_entry_count"] == 1
    assert (
        result.evidence["auto_recoverable_invalid_pending_entries"][0]
        ["matched_restart_commands"][0]
        ["boot_auto_cancelable_invalid_pending_entry"]
        is True
    )
    assert "boot auto-cancel" in result.detail


def test_live_actionable_certificate_semantics_allows_boot_terminal_no_fill_entry(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    trade_db = tmp_path / "zeus_trades.db"
    bin_label = "Will the highest temperature in Tokyo be 28°C on July 1?"
    payload = {
        **_valid_actionable_payload(),
        "event_id": "event-terminal-no-fill",
        "city": "Tokyo",
        "target_date": "2026-07-01",
        "temperature_metric": "high",
        "bin_label": bin_label,
        "direction": "buy_no",
        "token_id": "no-1",
        "q_live": 0.9045439679836288,
        "q_lcb_5pct": 0.8605040159563566,
        "c_fee_adjusted": 0.66,
        "trade_score": 0.2005040159563566,
        "action_score": 0.2005040159563566,
        "qkernel_execution_economics": {
            **_valid_actionable_payload()["qkernel_execution_economics"],
            "side": "NO",
            "payoff_q_point": 0.9045439679836288,
            "payoff_q_lcb": 0.8605040159563566,
            "cost": 0.66,
            "edge_lcb": 0.2005040159563566,
            "selection_guard_q_safe": 0.8605040159563566,
        },
    }
    _init_actionable_world_db(world_db, payload)
    _attach_forecast_parent(world_db)
    _init_parent_posterior_forecast_db(forecast_db, bin_label=bin_label)
    _init_entry_command_trade_db(
        trade_db,
        event_id="event-terminal-no-fill",
        token_id="no-1",
    )
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            cost_basis_usd REAL,
            chain_shares REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, cost_basis_usd, chain_shares
        ) VALUES ('pos-1', 'pending_entry', 0.0, 0.0, 0.0)
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            fact_id INTEGER PRIMARY KEY,
            command_id TEXT,
            venue_order_id TEXT,
            state TEXT,
            matched_size TEXT,
            remaining_size TEXT,
            local_sequence INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_order_facts (
            fact_id, command_id, venue_order_id, state,
            matched_size, remaining_size, local_sequence
        ) VALUES (1, 'cmd-1', 'venue-1', 'CANCEL_CONFIRMED', '0', '30.76', 1)
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)

    result = preflight._live_actionable_certificate_semantics_check()

    assert result.ok is True
    assert result.evidence["risky_count"] == 0
    assert result.evidence["historical_risky_count"] == 0
    assert result.evidence["auto_recoverable_terminal_no_fill_entry_count"] == 1
    recovered = result.evidence["auto_recoverable_terminal_no_fill_entries"][0]
    assert recovered["restart_recovery"] == (
        "boot_command_recovery_terminal_no_fill_before_reactor"
    )
    assert recovered["matched_restart_commands"][0]["boot_recoverable_terminal_no_fill_entry"] is True
    assert "terminal no-fill recovery" in result.detail


def test_live_actionable_certificate_semantics_allows_boot_invalid_open_entry_authority(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    trade_db = tmp_path / "zeus_trades.db"
    payload = {
        **_valid_actionable_payload(),
        "event_id": "event-open",
        "token_id": "yes-open",
        "city": "Buenos Aires",
        "target_date": "2026-07-02",
        "temperature_metric": "high",
        "bin_label": "Will the highest temperature in Buenos Aires be 11°C on July 2?",
        "direction": "buy_yes",
        "q_live": 0.248330938047289,
        "q_lcb_5pct": 0.099045130891989,
        "c_fee_adjusted": 0.041,
        "c_cost_95pct": 0.041,
        "trade_score": 0.058045130891989,
        "action_score": 0.058045130891989,
        "qkernel_execution_economics": {
            **_valid_actionable_payload()["qkernel_execution_economics"],
            "side": "YES",
            "payoff_q_point": 0.248330938047289,
            "payoff_q_lcb": 0.099045130891989,
            "cost": 0.041,
            "edge_lcb": 0.058045130891989,
            "selection_guard_q_safe": 0.099045130891989,
        },
    }
    _init_actionable_world_db(world_db, payload)
    _init_entry_command_trade_db(
        trade_db,
        event_id="event-open",
        token_id="yes-open",
        state="REVIEW_REQUIRED",
    )
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            cost_basis_usd REAL,
            chain_shares REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, cost_basis_usd, chain_shares
        ) VALUES ('pos-1', 'active', 69.34, 2.84294, 0.0)
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_trade_facts (
            command_id TEXT,
            venue_order_id TEXT,
            state TEXT,
            filled_size TEXT,
            fill_price TEXT,
            observed_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
            command_id, venue_order_id, state, filled_size, fill_price, observed_at
        ) VALUES ('cmd-1', 'venue-1', 'CONFIRMED', '69.34', '0.041', ?)
        """,
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", tmp_path / "missing-forecasts.db")

    result = preflight._live_actionable_certificate_semantics_check()

    assert result.ok is True
    assert result.evidence["risky_count"] == 0
    assert result.evidence["historical_risky_count"] == 0
    assert result.evidence["auto_recoverable_invalid_open_entry_authority_count"] == 1
    recovered = result.evidence["auto_recoverable_invalid_open_entry_authorities"][0]
    assert "ADMISSION_QKERNEL_CENTER_YES_QUALITY_FLOOR" in recovered["reason"]
    assert recovered["restart_recovery"] == (
        "boot_invalid_open_entry_authority_review_before_reactor"
    )
    assert (
        recovered["matched_restart_commands"][0]
        ["boot_recoverable_invalid_open_entry_authority"]
        is True
    )


def test_live_money_certificate_parent_modes_blocks_no_submit_parent(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(world_db)
    conn.execute(
        """
        CREATE TABLE decision_certificates (
            certificate_id TEXT PRIMARY KEY,
            certificate_hash TEXT NOT NULL,
            certificate_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            verifier_status TEXT NOT NULL,
            decision_time TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE decision_certificate_edges (
            child_certificate_id TEXT NOT NULL,
            parent_role TEXT NOT NULL,
            parent_certificate_hash TEXT NOT NULL,
            parent_certificate_type TEXT NOT NULL,
            required INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO decision_certificates VALUES (
            'exec-command-1', 'child-hash', 'ExecutionCommandCertificate',
            'LIVE', 'VERIFIED', ?, '{}'
        )
        """,
        (now,),
    )
    conn.execute(
        """
        INSERT INTO decision_certificates VALUES (
            'pre-submit-1', 'parent-hash', 'PreSubmitRevalidationCertificate',
            'NO_SUBMIT', 'VERIFIED', ?, '{}'
        )
        """,
        (now,),
    )
    conn.execute(
        """
        INSERT INTO decision_certificate_edges VALUES (
            'exec-command-1', 'pre_submit_revalidation', 'parent-hash',
            'PreSubmitRevalidationCertificate', 1, ?
        )
        """,
        (now,),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)

    result = preflight._live_money_certificate_parent_mode_check()

    assert result.ok is False
    assert result.evidence["risky_count"] == 1
    assert result.evidence["risky"][0]["child_certificate_type"] == "ExecutionCommandCertificate"
    assert "PreSubmitRevalidationCertificate:NO_SUBMIT" in result.evidence["risky"][0]["bad_parent_modes"]


def test_live_money_certificate_parent_modes_accepts_live_parent(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(world_db)
    conn.execute(
        """
        CREATE TABLE decision_certificates (
            certificate_id TEXT PRIMARY KEY,
            certificate_hash TEXT NOT NULL,
            certificate_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            verifier_status TEXT NOT NULL,
            decision_time TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE decision_certificate_edges (
            child_certificate_id TEXT NOT NULL,
            parent_role TEXT NOT NULL,
            parent_certificate_hash TEXT NOT NULL,
            parent_certificate_type TEXT NOT NULL,
            required INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO decision_certificates VALUES (
            'exec-command-1', 'child-hash', 'ExecutionCommandCertificate',
            'LIVE', 'VERIFIED', ?, '{}'
        )
        """,
        (now,),
    )
    conn.execute(
        """
        INSERT INTO decision_certificates VALUES (
            'pre-submit-1', 'parent-hash', 'PreSubmitRevalidationCertificate',
            'LIVE', 'VERIFIED', ?, '{}'
        )
        """,
        (now,),
    )
    conn.execute(
        """
        INSERT INTO decision_certificate_edges VALUES (
            'exec-command-1', 'pre_submit_revalidation', 'parent-hash',
            'PreSubmitRevalidationCertificate', 1, ?
        )
        """,
        (now,),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)

    result = preflight._live_money_certificate_parent_mode_check()

    assert result.ok is True
    assert result.evidence["risky_count"] == 0


def test_live_actionable_certificate_semantics_excludes_quarantined_invalid_rows(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    trade_db = tmp_path / "zeus_trades.db"
    payload = {
        **_valid_actionable_payload(),
        "q_live": 0.005,
        "q_lcb_5pct": 0.003,
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.22,
            "payoff_q_lcb": 0.05,
            "cost": 0.01,
            "edge_lcb": 0.04,
            "optimal_delta_u": 0.01,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 4.0,
            "false_edge_rate": 0.01,
            "direction_law_ok": False,
            "coherence_allows": True,
        },
    }
    _init_actionable_world_db(world_db, payload)
    trade = sqlite3.connect(trade_db)
    trade.execute(
        """
        CREATE TABLE decision_integrity_quarantine (
            table_name TEXT NOT NULL,
            row_id TEXT NOT NULL,
            reason_code TEXT NOT NULL
        )
        """
    )
    trade.execute(
        """
        INSERT INTO decision_integrity_quarantine (table_name, row_id, reason_code)
        VALUES (?, 'hash-test', ?)
        """,
        (DECISION_CERTIFICATES_TABLE, REASON_INVALID_LIVE_ACTIONABLE),
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)

    result = preflight._live_actionable_certificate_semantics_check()

    assert result.ok is True
    assert result.evidence["checked_count"] == 1
    assert result.evidence["risky_count"] == 0
    assert result.evidence["quarantined_risky_count"] == 1


def test_day0_belief_preflight_accepts_hko_canonical_observation_without_monitor_event(
    monkeypatch, tmp_path
):
    world_db = tmp_path / "zeus-world.db"
    world = sqlite3.connect(world_db)
    world.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT, target_date TEXT, local_timestamp TEXT, utc_timestamp TEXT,
            running_max REAL, running_min REAL, authority TEXT, causality_status TEXT,
            source TEXT, temperature_metric TEXT, training_allowed INTEGER, source_role TEXT
        )
        """
    )
    world.execute(
        """
        INSERT INTO observation_instants VALUES (
            'Hong Kong', '2026-06-26', '2026-06-26T07:00:00+08:00',
            '2026-06-25T23:00Z', 27.0, 27.0, 'ICAO_STATION_NATIVE',
            'OK', 'hko_hourly_accumulator', 'low', 0, 'runtime_monitoring'
        )
        """
    )
    world.commit()
    world.close()
    row_conn = sqlite3.connect(":memory:")
    row_conn.row_factory = sqlite3.Row
    row_conn.execute(
        """
        CREATE TABLE p (
            position_id TEXT, phase TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, bin_label TEXT, direction TEXT
        )
        """
    )
    row_conn.execute(
        """
        INSERT INTO p VALUES (
            'pos-hk', 'day0_window', 'Hong Kong', '2026-06-26',
            'low', 'Will the lowest temperature in Hong Kong be 28°C on June 26?',
            'buy_no'
        )
        """
    )
    row = row_conn.execute("SELECT * FROM p").fetchone()
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)

    evidence = preflight._day0_canonical_observation_evidence(
        row,
        now=datetime(2026, 6, 26, 1, 0, 0, tzinfo=timezone.utc),
    )

    assert evidence is not None
    assert evidence["observed_extreme"] == 27.0
    assert evidence["source"] == "world.observation_instants"


def test_live_order_presubmit_shape_blocks_restart_relevant_old_payload(monkeypatch, tmp_path):
    world_db = tmp_path / "zeus-world.db"
    conn = _init_live_order_world_db(world_db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "evt-1",
            "agg-1",
            1,
            "PreSubmitRevalidated",
            None,
            "hash-1",
            json.dumps({"event_id": "event-1", "direction": "buy_no", "limit_price": 0.98}),
            "payload-hash-1",
            "engine_adapter",
            now,
            now,
            1,
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_projection (
            aggregate_id, event_id, final_intent_id, current_state,
            last_sequence, last_event_type, last_event_hash,
            pending_reconcile, venue_order_id, updated_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "agg-1",
            "event-1",
            "intent-1",
            "EXECUTION_COMMAND_CREATED",
            1,
            "PreSubmitRevalidated",
            "hash-1",
            0,
            "0xabc",
            now,
            1,
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)

    result = preflight._edli_live_order_presubmit_shape_check()

    assert result.ok is False
    assert result.evidence["missing_count"] == 1
    assert result.evidence["samples"][0]["aggregate_id"] == "agg-1"


def test_live_order_presubmit_shape_ignores_terminal_old_payload(monkeypatch, tmp_path):
    world_db = tmp_path / "zeus-world.db"
    conn = _init_live_order_world_db(world_db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "evt-1",
            "agg-1",
            1,
            "PreSubmitRevalidated",
            None,
            "hash-1",
            json.dumps({"event_id": "event-1", "direction": "buy_no", "limit_price": 0.98}),
            "payload-hash-1",
            "engine_adapter",
            now,
            now,
            1,
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_projection (
            aggregate_id, event_id, final_intent_id, current_state,
            last_sequence, last_event_type, last_event_hash,
            pending_reconcile, venue_order_id, updated_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "agg-1",
            "event-1",
            "intent-1",
            "RECONCILED",
            1,
            "PreSubmitRevalidated",
            "hash-1",
            0,
            "",
            now,
            1,
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)

    result = preflight._edli_live_order_presubmit_shape_check()

    assert result.ok is True
    assert result.evidence["missing_count"] == 0


def test_live_order_presubmit_shape_blocks_boot_recoverable_current_command(monkeypatch, tmp_path):
    world_db = tmp_path / "zeus-world.db"
    trade_db = tmp_path / "zeus_trades.db"
    trade_conn = sqlite3.connect(trade_db)
    trade_conn.execute("CREATE TABLE venue_commands (command_id TEXT, decision_id TEXT, state TEXT, venue_order_id TEXT)")
    trade_conn.execute("CREATE TABLE venue_trade_facts (command_id TEXT, filled_size TEXT)")
    trade_conn.commit()
    trade_conn.close()

    conn = _init_live_order_world_db(world_db)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            "evt-1",
            "agg-1",
            1,
            "PreSubmitRevalidated",
            None,
            "hash-1",
            json.dumps({"event_id": "event-1", "direction": "buy_no", "limit_price": 0.68}),
            "payload-hash-1",
            "engine_adapter",
            now,
            now,
            1,
        ),
        (
            "evt-2",
            "agg-1",
            2,
            "ExecutionCommandCreated",
            "hash-1",
            "hash-2",
            json.dumps(
                {
                    "event_id": "event-1",
                    "final_intent_id": "intent-1",
                    "execution_command_id": "cmd-1",
                }
            ),
            "payload-hash-2",
            "engine_adapter",
            now,
            now,
            1,
        ),
    ]
    conn.executemany(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_projection (
            aggregate_id, event_id, final_intent_id, current_state,
            last_sequence, last_event_type, last_event_hash,
            pending_reconcile, venue_order_id, updated_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "agg-1",
            "event-1",
            "intent-1",
            "EXECUTION_COMMAND_CREATED",
            2,
            "ExecutionCommandCreated",
            "hash-2",
            0,
            "",
            now,
            1,
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)

    result = preflight._edli_live_order_presubmit_shape_check()

    assert result.ok is False
    assert result.evidence["missing_count"] == 1
    assert result.evidence["boot_recoverable_count"] == 0
    assert result.evidence["unsubmitted_ghost_recoverable_count"] == 0
    assert result.evidence["unsafe_count"] == 1
    assert (
        result.evidence["restart_policy"]
        == "fail_closed_restart_relevant_presubmit_requires_current_entry_economics"
    )


def _init_entry_provenance_trade_db(path, *, submit_payload: dict[str, object]) -> None:
    conn = _init_trade_db(path)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            snapshot_id TEXT,
            envelope_id TEXT,
            position_id TEXT,
            decision_id TEXT,
            idempotency_key TEXT,
            intent_kind TEXT,
            market_id TEXT,
            token_id TEXT,
            side TEXT,
            size REAL,
            price REAL,
            venue_order_id TEXT,
            state TEXT,
            last_event_id TEXT,
            created_at TEXT,
            updated_at TEXT,
            review_required_reason TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_command_events (
            event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT,
            state_after TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            bin_label, direction, shares, chain_shares, order_status,
            exit_reason, exit_retry_count, next_exit_retry_at,
            last_monitor_prob, last_monitor_prob_is_fresh,
            last_monitor_market_price, last_monitor_market_price_is_fresh,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "pos-1",
            "active",
            "Lucknow",
            "2026-06-28",
            "high",
            "35C or below",
            "buy_yes",
            20.0,
            20.0,
            "partial",
            None,
            0,
            None,
            0.80,
            1,
            0.006,
            1,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-1",
            "snap-1",
            "env-1",
            "pos-1",
            "decision-1",
            "idem-1",
            "ENTRY",
            "market-1",
            "token-1",
            "BUY",
            100.0,
            0.006,
            "0xabc",
            "EXPIRED",
            "event-1",
            now,
            now,
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO venue_command_events (
            event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-1",
            "cmd-1",
            1,
            "SUBMIT_REQUESTED",
            now,
            json.dumps(submit_payload),
            "REQUESTED",
        ),
    )
    conn.commit()
    conn.close()


def test_position_projection_integrity_blocks_edli_legacy_projection(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_entry_provenance_trade_db(
        trade_db,
        submit_payload={"execution_capability": {"components": []}},
    )
    trade = sqlite3.connect(trade_db)
    trade.execute("ALTER TABLE position_current ADD COLUMN entry_method TEXT")
    trade.execute("ALTER TABLE position_current ADD COLUMN p_posterior REAL")
    trade.execute("ALTER TABLE position_current ADD COLUMN cost_basis_usd REAL")
    trade.execute(
        """
        UPDATE position_current
           SET entry_method = 'ens_member_counting',
               p_posterior = 0.0,
               cost_basis_usd = 0.1192
         WHERE position_id = 'pos-1'
        """
    )
    trade.execute(
        """
        UPDATE venue_commands
           SET decision_id = 'edli_exec_cmd:edli_evt_lucknow:intent:token-1:token-1:buy_yes'
         WHERE command_id = 'cmd-1'
        """
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._position_current_projection_integrity_check(
        preflight._open_positions()
    )

    assert result.ok is False
    assert result.evidence["risky"][0]["risk"] == "edli_entry_projected_without_qkernel_authority"
    assert result.evidence["risky"][0]["position_id"] == "pos-1"


def test_position_projection_integrity_blocks_hard_terminal_reactivation(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_entry_provenance_trade_db(
        trade_db,
        submit_payload={"execution_capability": {"components": []}},
    )
    now = datetime.now(timezone.utc).isoformat()
    trade = sqlite3.connect(trade_db)
    trade.execute("ALTER TABLE position_current ADD COLUMN entry_method TEXT")
    trade.execute("ALTER TABLE position_current ADD COLUMN p_posterior REAL")
    trade.execute("ALTER TABLE position_current ADD COLUMN cost_basis_usd REAL")
    trade.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            phase_before TEXT,
            phase_after TEXT,
            payload_json TEXT
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at,
            phase_before, phase_after, payload_json
        ) VALUES (
            'ev-terminal', 'pos-1', 7, 'ADMIN_VOIDED', ?, 'pending_exit', 'voided', '{}'
        )
        """,
        (now,),
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._position_current_projection_integrity_check(
        preflight._open_positions()
    )

    assert result.ok is False
    assert result.evidence["risky"][0]["risk"] == "open_position_after_hard_terminal_event"
    assert result.evidence["risky"][0]["terminal_event"]["event_type"] == "ADMIN_VOIDED"


def test_position_projection_integrity_blocks_terminal_chain_exposure_projection(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_entry_provenance_trade_db(
        trade_db,
        submit_payload={"execution_capability": {"components": []}},
    )
    now = datetime.now(timezone.utc).isoformat()
    trade = sqlite3.connect(trade_db)
    trade.execute("ALTER TABLE position_current ADD COLUMN entry_method TEXT")
    trade.execute("ALTER TABLE position_current ADD COLUMN p_posterior REAL")
    trade.execute("ALTER TABLE position_current ADD COLUMN cost_basis_usd REAL")
    trade.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               chain_shares = 9.0,
               entry_method = 'ens_member_counting',
               p_posterior = 0.0,
               cost_basis_usd = 6.30
         WHERE position_id = 'pos-1'
        """
    )
    trade.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            phase_before TEXT,
            phase_after TEXT,
            payload_json TEXT
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at,
            phase_before, phase_after, payload_json
        ) VALUES (
            'ev-terminal', 'pos-1', 7, 'ADMIN_VOIDED', ?, 'pending_exit', 'voided', '{}'
        )
        """,
        (now,),
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._position_current_projection_integrity_check(
        preflight._open_positions()
    )

    assert result.ok is False
    assert result.evidence["covered_count"] == 0
    assert result.evidence["risky"][0]["risk"] == "terminal_position_with_positive_chain_exposure"
    assert result.evidence["risky"][0]["chain_shares"] == 9.0


def test_position_projection_integrity_allows_superseded_phantom_void_recovery(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_entry_provenance_trade_db(
        trade_db,
        submit_payload={"execution_capability": {"components": []}},
    )
    now = datetime.now(timezone.utc).isoformat()
    trade = sqlite3.connect(trade_db)
    trade.execute("ALTER TABLE position_current ADD COLUMN entry_method TEXT")
    trade.execute("ALTER TABLE position_current ADD COLUMN p_posterior REAL")
    trade.execute("ALTER TABLE position_current ADD COLUMN cost_basis_usd REAL")
    trade.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            phase_before TEXT,
            phase_after TEXT,
            payload_json TEXT
        )
        """
    )
    trade.executemany(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at,
            phase_before, phase_after, payload_json
        ) VALUES (?, 'pos-1', ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "ev-terminal",
                7,
                "ADMIN_VOIDED",
                now,
                "pending_exit",
                "voided",
                '{"reason":"PHANTOM_NOT_ON_CHAIN"}',
            ),
            (
                "ev-recovery",
                8,
                "REVIEW_REQUIRED",
                now,
                "voided",
                "quarantined",
                '{"reason":"chain_absent_confirmed_position_unattributed"}',
            ),
            (
                "ev-chain-positive",
                9,
                "CHAIN_SIZE_CORRECTED",
                now,
                "pending_exit",
                "pending_exit",
                '{"chain_state":"synced","chain_shares_after":85.17}',
            ),
        ],
    )
    trade.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               entry_method = 'qkernel_spine',
               p_posterior = 0.21,
               cost_basis_usd = 4.34,
               chain_shares = 85.17
         WHERE position_id = 'pos-1'
        """
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._position_current_projection_integrity_check(
        preflight._open_positions()
    )

    assert result.ok is True
    assert result.evidence["risky"] == []


def test_preflight_projection_integrity_checks_zero_chain_open_ghost(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_entry_provenance_trade_db(
        trade_db,
        submit_payload={"execution_capability": {"components": []}},
    )
    now = datetime.now(timezone.utc).isoformat()
    trade = sqlite3.connect(trade_db)
    trade.execute(
        """
        UPDATE position_current
           SET shares = 19.0,
               chain_shares = 0.0
         WHERE position_id = 'pos-1'
        """
    )
    trade.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            phase_before TEXT,
            phase_after TEXT,
            payload_json TEXT
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at,
            phase_before, phase_after, payload_json
        ) VALUES (
            'ev-terminal-zero-chain', 'pos-1', 7, 'ADMIN_VOIDED',
            ?, 'pending_exit', 'voided', '{}'
        )
        """,
        (now,),
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._position_current_projection_integrity_check(
        preflight._open_positions(positive_chain_only=False)
    )

    assert preflight._open_positions() == []
    assert result.ok is False
    assert result.evidence["risky"][0]["risk"] == "open_position_after_hard_terminal_event"


def test_preflight_open_positions_include_terminal_phase_positive_chain_exposure(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_entry_provenance_trade_db(
        trade_db,
        submit_payload={"execution_capability": {"components": []}},
    )
    trade = sqlite3.connect(trade_db)
    trade.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               shares = 0.0,
               chain_shares = 10.7
         WHERE position_id = 'pos-1'
        """
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    rows = preflight._open_positions()

    assert [row["position_id"] for row in rows] == ["pos-1"]
    assert rows[0]["phase"] == "voided"
    assert rows[0]["chain_shares"] == 10.7


def test_preflight_open_positions_exclude_quarantined_zero_chain_local_ghost(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_entry_provenance_trade_db(
        trade_db,
        submit_payload={"execution_capability": {"components": []}},
    )
    trade = sqlite3.connect(trade_db)
    trade.execute("ALTER TABLE position_current ADD COLUMN cost_basis_usd REAL")
    trade.execute(
        """
        UPDATE position_current
           SET phase = 'quarantined',
               shares = 19.0,
               chain_shares = 0.0,
               cost_basis_usd = 11.40
         WHERE position_id = 'pos-1'
        """
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    assert preflight._open_positions() == []


def test_preflight_open_positions_exclude_economically_closed_stale_chain_projection(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_entry_provenance_trade_db(
        trade_db,
        submit_payload={"execution_capability": {"components": []}},
    )
    trade = sqlite3.connect(trade_db)
    trade.execute(
        """
        UPDATE position_current
           SET phase = 'economically_closed',
               shares = 0.0,
               chain_shares = 10.7
         WHERE position_id = 'pos-1'
        """
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    assert preflight._open_positions() == []
    result = preflight._economically_closed_sell_projection_exposure_check()
    assert result.ok is True


def test_preflight_blocks_sell_filled_closed_projection_with_positive_chain_exposure(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_entry_provenance_trade_db(
        trade_db,
        submit_payload={"execution_capability": {"components": []}},
    )
    trade = sqlite3.connect(trade_db)
    trade.execute(
        """
        UPDATE position_current
           SET phase = 'economically_closed',
               order_status = 'sell_filled',
               shares = 10.7,
               chain_shares = 10.7
         WHERE position_id = 'pos-1'
        """
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    assert preflight._open_positions() == []
    result = preflight._economically_closed_sell_projection_exposure_check()
    assert result.ok is False
    assert result.detail == (
        "economically closed sell-filled projections still carry positive chain exposure"
    )
    assert result.evidence["risky"][0]["position_id"] == "pos-1"


def test_preflight_open_positions_exclude_settled_stale_quarantine_chain_state(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_entry_provenance_trade_db(
        trade_db,
        submit_payload={"execution_capability": {"components": []}},
    )
    trade = sqlite3.connect(trade_db)
    trade.execute("ALTER TABLE position_current ADD COLUMN chain_state TEXT")
    trade.execute(
        """
        UPDATE position_current
           SET phase = 'settled',
               chain_state = 'entry_authority_quarantined',
               shares = 0.0,
               chain_shares = 10.7
         WHERE position_id = 'pos-1'
        """
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    assert preflight._open_positions() == []


def test_execution_feasibility_allows_canonical_day0_without_quote_table(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    _init_entry_provenance_trade_db(
        trade_db,
        submit_payload={"execution_capability": {"components": []}},
    )
    trade = sqlite3.connect(trade_db)
    trade.execute(
        """
        UPDATE position_current
           SET phase='day0_window',
               city='Hong Kong',
               target_date='2026-06-26',
               temperature_metric='low',
               bin_label='Will the lowest temperature in Hong Kong be 28°C on June 26?',
               direction='buy_no'
         WHERE position_id='pos-1'
        """
    )
    trade.commit()
    trade.close()
    world = sqlite3.connect(world_db)
    world.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT, target_date TEXT, local_timestamp TEXT, utc_timestamp TEXT,
            running_max REAL, running_min REAL, authority TEXT, causality_status TEXT,
            source TEXT, temperature_metric TEXT, training_allowed INTEGER, source_role TEXT
        )
        """
    )
    world.execute(
        """
        INSERT INTO observation_instants VALUES (
            'Hong Kong', '2026-06-26', '2026-06-26T07:00:00+08:00',
            '2026-06-25T23:00Z', 27.0, 27.0, 'ICAO_STATION_NATIVE',
            'OK', 'hko_hourly_accumulator', 'low', 0, 'runtime_monitoring'
        )
        """
    )
    world.commit()
    world.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)

    result = preflight._execution_feasibility_evidence_check(preflight._open_positions())

    assert result.ok is True
    assert result.evidence["row_count"] == "not_scanned_no_quote_required_after_canonical_day0"
    assert result.evidence["covered"][0]["restart_resolution"] == (
        "boot_monitor_refresh_from_canonical_day0_observation"
    )
    assert result.evidence["risky"] == []


def _init_resting_command_trade_db(path, *, phase: str, intent_kind: str = "EXIT") -> None:
    conn = _init_trade_db(path)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            intent_kind TEXT,
            position_id TEXT,
            state TEXT,
            venue_order_id TEXT,
            price REAL,
            size REAL,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            command_id TEXT,
            state TEXT,
            observed_at TEXT,
            venue_order_id TEXT,
            matched_size TEXT,
            remaining_size TEXT,
            raw_payload_json TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            bin_label, direction, shares, chain_shares, order_status,
            exit_reason, exit_retry_count, next_exit_retry_at,
            last_monitor_prob, last_monitor_prob_is_fresh,
            last_monitor_market_price, last_monitor_market_price_is_fresh,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "pos-1",
            phase,
            "Singapore",
            "2026-06-27",
            "high",
            "32C",
            "buy_no",
            12.0,
            12.0,
            "filled",
            None,
            0,
            None,
            0.80,
            1,
            0.49,
            1,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, intent_kind, position_id, state, venue_order_id,
            price, size, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("cmd-1", intent_kind, "pos-1", "ACKED", "0xabc", 0.49, 12.0, now, now),
    )
    conn.execute(
        """
        INSERT INTO venue_order_facts (
            command_id, state, observed_at, venue_order_id,
            matched_size, remaining_size, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("cmd-1", "LIVE", now, "0xabc", "0", "12.0", "{}"),
    )
    conn.commit()
    conn.close()


def test_resting_exit_order_is_boot_recoverable_when_position_not_pending_exit(monkeypatch, tmp_path):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_resting_command_trade_db(trade_db, phase="quarantined")
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._resting_venue_command_lifecycle_alignment_check()

    assert result.ok is True
    assert result.evidence["boot_recoverable"][0]["risk"] == "resting_exit_order_without_pending_exit_lifecycle"
    assert (
        result.evidence["boot_recoverable"][0]["repair_action"]
        == "restore_position_pending_exit_for_live_exit_order"
    )


def test_resting_exit_order_blocks_when_phase_is_not_boot_recoverable(monkeypatch, tmp_path):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_resting_command_trade_db(trade_db, phase="settled")
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._resting_venue_command_lifecycle_alignment_check()

    assert result.ok is False
    assert result.evidence["risky"][0]["risk"] == "resting_exit_order_without_pending_exit_lifecycle"


def test_resting_entry_order_is_boot_recoverable_when_projection_repair_can_hydrate():
    item = {
        "command_id": "cmd-entry",
        "intent_kind": "ENTRY",
        "latest_fact_state": "PARTIALLY_MATCHED",
        "position_phase": None,
    }

    recoverable = preflight._resting_venue_command_boot_recoverable(
        item,
        "resting_entry_order_without_entry_lifecycle",
        entry_projection_recoverable={
            "cmd-entry": {
                "restart_resolution": "command_recovery.filled_entry_projection_repair",
                "repair_action": "project_partial_or_filled_entry_order_into_active_position",
                "city": "Moscow",
                "target_date": "2026-07-01",
                "direction": "buy_no",
                "bin_label": "Moscow 28C",
            }
        },
    )

    assert recoverable is not None
    assert recoverable["risk"] == "resting_entry_order_without_entry_lifecycle"
    assert recoverable["restart_resolution"] == "command_recovery.filled_entry_projection_repair"
    assert recoverable["repair_action"] == "project_partial_or_filled_entry_order_into_active_position"


def test_resting_entry_terminal_no_fill_fact_is_boot_recoverable(monkeypatch, tmp_path):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_resting_command_trade_db(
        trade_db,
        phase="pending_entry",
        intent_kind="ENTRY",
    )
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        UPDATE position_current
           SET shares = 0.0,
               chain_shares = 0.0,
               order_status = 'acked'
         WHERE position_id = 'pos-1'
        """
    )
    conn.execute(
        """
        UPDATE venue_order_facts
           SET state = 'CANCEL_CONFIRMED',
               matched_size = '0',
               remaining_size = '26.18'
         WHERE command_id = 'cmd-1'
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._resting_venue_command_lifecycle_alignment_check()

    assert result.ok is True
    assert result.evidence["risky"] == []
    assert result.evidence["boot_recoverable"][0]["risk"] == (
        "command_projection_stale_after_terminal_venue_fact"
    )
    assert result.evidence["boot_recoverable"][0]["restart_resolution"] == (
        "command_recovery.terminal_order_fact_no_fill"
    )
    assert result.evidence["boot_recoverable"][0]["repair_action"] == (
        "reconcile_terminal_order_facts"
    )


def test_resting_entry_terminal_positive_fact_is_boot_recoverable(monkeypatch, tmp_path):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_resting_command_trade_db(
        trade_db,
        phase="active",
        intent_kind="ENTRY",
    )
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        UPDATE position_current
           SET chain_shares = 8.51,
               shares = 8.51,
               order_status = 'filled'
         WHERE position_id = 'pos-1'
        """
    )
    conn.execute(
        """
        UPDATE venue_order_facts
           SET state = 'MATCHED',
               matched_size = '8.51',
               remaining_size = '0'
         WHERE command_id = 'cmd-1'
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._resting_venue_command_lifecycle_alignment_check()

    assert result.ok is True
    assert result.evidence["risky"] == []
    assert result.evidence["boot_recoverable"][0]["risk"] == (
        "command_projection_stale_after_terminal_venue_fact"
    )
    assert result.evidence["boot_recoverable"][0]["restart_resolution"] == (
        "command_recovery.matched_cancel_review_required_entries"
    )
    assert result.evidence["boot_recoverable"][0]["repair_action"] == (
        "terminalize_entry_command_from_positive_match_fact"
    )


def test_review_required_entry_with_positive_trade_fact_is_boot_recoverable(
    monkeypatch,
    tmp_path,
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_resting_command_trade_db(
        trade_db,
        phase="active",
        intent_kind="ENTRY",
    )
    conn = sqlite3.connect(trade_db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE venue_commands
           SET state = 'REVIEW_REQUIRED'
         WHERE command_id = 'cmd-1'
        """
    )
    conn.execute(
        """
        UPDATE venue_order_facts
           SET state = 'LIVE',
               matched_size = '0',
               remaining_size = '133.16'
         WHERE command_id = 'cmd-1'
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_trade_facts (
            command_id TEXT,
            venue_order_id TEXT,
            state TEXT,
            filled_size TEXT,
            fill_price TEXT,
            observed_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
            command_id, venue_order_id, state, filled_size, fill_price, observed_at
        ) VALUES (
            'cmd-1', '0xabc', 'CONFIRMED', '69.34', '0.041', ?
        )
        """,
        (now,),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._resting_venue_command_lifecycle_alignment_check()

    assert result.ok is True
    assert result.evidence["risky"] == []
    assert result.evidence["covered_count"] == 0
    recoverable = result.evidence["boot_recoverable"][0]
    assert recoverable["risk"] == "review_required_entry_with_positive_trade_fact"
    assert recoverable["restart_resolution"] == (
        "command_recovery.matched_cancel_review_required_entries"
    )
    assert recoverable["repair_action"] == (
        "terminalize_review_required_entry_from_positive_trade_fact"
    )


def test_resting_exit_order_allows_pending_exit(monkeypatch, tmp_path):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    _init_resting_command_trade_db(trade_db, phase="pending_exit")
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)

    result = preflight._resting_venue_command_lifecycle_alignment_check()

    assert result.ok is True
    assert result.evidence["covered_count"] == 1


class _FakeVenuePointAdapter:
    def __init__(self, orders: dict[str, dict], *, point_errors: set[str] | None = None):
        self.orders = orders
        self.point_errors = set(point_errors or ())

    def get_order(self, order_id: str):
        if order_id in self.point_errors:
            raise RuntimeError("point read failed")
        return self.orders.get(order_id)

    def get_open_orders(self):
        return list(self.orders.values())


class _FakeVenueClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _init_entry_venue_audit_db(path, *, command_state="ACKED", fact_state="LIVE", matched_size="0"):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            decision_id TEXT,
            intent_kind TEXT,
            side TEXT,
            token_id TEXT,
            state TEXT,
            venue_order_id TEXT,
            size REAL,
            price REAL,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            command_id TEXT,
            venue_order_id TEXT,
            state TEXT,
            matched_size TEXT,
            remaining_size TEXT,
            observed_at TEXT
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, decision_id, intent_kind, side, token_id,
            state, venue_order_id, size, price, created_at, updated_at
        ) VALUES (
            'cmd-venue-audit', 'pos-venue-audit', 'edli_exec_cmd:event-venue-audit',
            'ENTRY', 'BUY', 'token-no-1', ?, 'venue-order-1', 10.58, 0.67, ?, ?
        )
        """,
        (command_state, now, now),
    )
    conn.execute(
        """
        INSERT INTO venue_order_facts (
            command_id, venue_order_id, state, matched_size, remaining_size, observed_at
        ) VALUES (
            'cmd-venue-audit', 'venue-order-1', ?, ?, '10.58', ?
        )
        """,
        (fact_state, matched_size, now),
    )
    conn.commit()
    conn.close()


def test_venue_point_order_truth_alignment_uses_local_terminal_no_fill_fact_without_venue_read(
    monkeypatch,
    tmp_path,
):
    trade_db = tmp_path / "zeus_trades.db"
    _init_entry_venue_audit_db(
        trade_db,
        fact_state="CANCEL_CONFIRMED",
        matched_size="0",
    )
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            cost_basis_usd REAL,
            chain_shares REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, cost_basis_usd, chain_shares
        ) VALUES ('pos-venue-audit', 'pending_entry', 0.0, 0.0, 0.0)
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)

    def _unexpected_venue_reader():
        raise AssertionError("local terminal no-fill fact must not require venue read")

    monkeypatch.setattr(preflight, "_preflight_venue_adapter", _unexpected_venue_reader)

    result = preflight._venue_point_order_truth_alignment_check()

    assert result.ok is True
    assert result.evidence["risky"] == []
    assert result.evidence["venue_read_command_count"] == 0
    assert result.evidence["local_terminal_no_fill_boot_recoverable_count"] == 1
    assert result.evidence["boot_recoverable"][0]["risk"] == (
        "venue_terminal_no_fill_not_projected_locally"
    )
    assert result.evidence["boot_recoverable"][0]["repair_action"] == (
        "edli_boot_command_recovery_live_tick_terminal_no_fill"
    )
    assert result.evidence["boot_recoverable"][0]["restart_resolution"] == (
        "command_recovery.terminal_order_fact_no_fill"
    )


def test_venue_point_order_truth_alignment_marks_live_positive_match_boot_recoverable(
    monkeypatch,
    tmp_path,
):
    trade_db = tmp_path / "zeus_trades.db"
    _init_entry_venue_audit_db(trade_db, fact_state="LIVE", matched_size="0")
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    fake_client = _FakeVenueClient()
    fake_adapter = _FakeVenuePointAdapter(
        {
            "venue-order-1": {
                "id": "venue-order-1",
                "status": "LIVE",
                "size_matched": "4.484847",
                "original_size": "10.58",
                "price": "0.67",
            }
        }
    )
    monkeypatch.setattr(preflight, "_preflight_venue_adapter", lambda: (fake_client, fake_adapter))

    result = preflight._venue_point_order_truth_alignment_check()

    assert result.ok is True
    assert result.evidence["risky"] == []
    assert result.evidence["boot_recoverable"][0]["risk"] == "venue_positive_match_not_projected_locally"
    assert result.evidence["boot_recoverable"][0]["venue_status"] == "LIVE"
    assert result.evidence["boot_recoverable"][0]["venue_matched_size"] == 4.484847
    assert (
        result.evidence["boot_recoverable"][0]["repair_action"]
        == "edli_boot_command_recovery_live_tick_matched_order_facts"
    )
    assert fake_client.closed is True


def test_venue_point_order_truth_alignment_uses_open_orders_fallback_on_point_timeout(
    monkeypatch,
    tmp_path,
):
    trade_db = tmp_path / "zeus_trades.db"
    _init_entry_venue_audit_db(trade_db, fact_state="LIVE", matched_size="0")
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    fake_adapter = _FakeVenuePointAdapter(
        {
            "venue-order-1": {
                "id": "venue-order-1",
                "status": "LIVE",
                "size_matched": "4.484847",
                "original_size": "10.58",
                "price": "0.67",
            }
        },
        point_errors={"venue-order-1"},
    )
    monkeypatch.setattr(preflight, "_preflight_venue_adapter", lambda: (_FakeVenueClient(), fake_adapter))

    result = preflight._venue_point_order_truth_alignment_check()

    assert result.ok is True
    assert result.evidence["risky"] == []
    assert result.evidence["boot_recoverable"][0]["risk"] == "venue_positive_match_not_projected_locally"
    assert result.evidence["boot_recoverable"][0]["venue_status"] == "LIVE"


def test_venue_point_order_truth_alignment_blocks_when_point_and_open_reads_fail(
    monkeypatch,
    tmp_path,
):
    trade_db = tmp_path / "zeus_trades.db"
    _init_entry_venue_audit_db(trade_db, fact_state="LIVE", matched_size="0")
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)

    class FailingAdapter:
        def get_order(self, order_id: str):
            raise RuntimeError("point read failed")

        def get_open_orders(self):
            raise RuntimeError("open read failed")

    monkeypatch.setattr(preflight, "_preflight_venue_adapter", lambda: (_FakeVenueClient(), FailingAdapter()))

    result = preflight._venue_point_order_truth_alignment_check()

    assert result.ok is False
    assert result.evidence["risky"][0]["risk"] == "venue_point_order_read_failed"


def test_venue_point_order_truth_alignment_blocks_unknown_point_status(
    monkeypatch,
    tmp_path,
):
    trade_db = tmp_path / "zeus_trades.db"
    _init_entry_venue_audit_db(trade_db, fact_state="LIVE", matched_size="0")
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    fake_adapter = _FakeVenuePointAdapter(
        {
            "venue-order-1": {
                "id": "venue-order-1",
                "status": "UNKNOWN",
            }
        }
    )
    fake_adapter.orders = {}
    fake_adapter.get_order = lambda order_id: {"id": order_id, "status": "UNKNOWN"}
    monkeypatch.setattr(preflight, "_preflight_venue_adapter", lambda: (_FakeVenueClient(), fake_adapter))

    result = preflight._venue_point_order_truth_alignment_check()

    assert result.ok is False
    assert result.evidence["risky"][0]["risk"] == "venue_point_order_status_unknown"


def test_venue_point_order_truth_alignment_boot_recovers_unknown_status_with_positive_trade(
    monkeypatch,
    tmp_path,
):
    trade_db = tmp_path / "zeus_trades.db"
    _init_entry_venue_audit_db(
        trade_db,
        command_state="REVIEW_REQUIRED",
        fact_state="LIVE",
        matched_size="0",
    )
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            cost_basis_usd REAL,
            chain_shares REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, cost_basis_usd, chain_shares
        ) VALUES ('pos-venue-audit', 'active', 69.34, 2.84294, 0.0)
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_trade_facts (
            command_id TEXT,
            venue_order_id TEXT,
            state TEXT,
            filled_size TEXT,
            fill_price TEXT,
            observed_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
            command_id, venue_order_id, state, filled_size, fill_price, observed_at
        ) VALUES (
            'cmd-venue-audit', 'venue-order-1', 'CONFIRMED', '69.34', '0.041', ?
        )
        """,
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    fake_adapter = _FakeVenuePointAdapter({})
    fake_adapter.get_order = lambda order_id: {"id": order_id, "status": "UNKNOWN"}
    monkeypatch.setattr(
        preflight,
        "_preflight_venue_adapter",
        lambda: (_FakeVenueClient(), fake_adapter),
    )

    result = preflight._venue_point_order_truth_alignment_check()

    assert result.ok is True
    assert result.evidence["risky"] == []
    recoverable = result.evidence["boot_recoverable"][0]
    assert recoverable["risk"] == "venue_point_order_status_unknown"
    assert recoverable["restart_resolution"] == (
        "command_recovery.matched_cancel_review_required_entries"
    )
    assert recoverable["repair_action"] == (
        "terminalize_review_required_entry_from_positive_trade_fact"
    )


def test_venue_point_order_truth_alignment_accepts_projected_partial_match(
    monkeypatch,
    tmp_path,
):
    trade_db = tmp_path / "zeus_trades.db"
    _init_entry_venue_audit_db(
        trade_db,
        command_state="PARTIAL",
        fact_state="PARTIALLY_MATCHED",
        matched_size="4.484847",
    )
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    fake_adapter = _FakeVenuePointAdapter(
        {
            "venue-order-1": {
                "id": "venue-order-1",
                "status": "LIVE",
                "size_matched": "4.484847",
                "original_size": "10.58",
                "price": "0.67",
            }
        }
    )
    monkeypatch.setattr(preflight, "_preflight_venue_adapter", lambda: (_FakeVenueClient(), fake_adapter))

    result = preflight._venue_point_order_truth_alignment_check()

    assert result.ok is True
    assert result.evidence["covered_count"] == 1
    assert result.evidence["risky"] == []


def test_runtime_state_dir_reads_primary_root_from_live_plist(monkeypatch, tmp_path):
    monkeypatch.delenv("ZEUS_LIVE_PREFLIGHT_STATE_DIR", raising=False)
    monkeypatch.delenv("ZEUS_STATE_DIR", raising=False)
    monkeypatch.delenv("ZEUS_PRIMARY_ROOT", raising=False)
    runtime_root = tmp_path / "runtime-root"
    plist = tmp_path / "com.zeus.live-trading.plist"
    plist.write_bytes(
        (
            b"""<?xml version="1.0" encoding="UTF-8"?>\n"""
            b"""<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" """
            b""""http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n"""
            b"""<plist version="1.0"><dict><key>EnvironmentVariables</key><dict>"""
            + f"<key>ZEUS_PRIMARY_ROOT</key><string>{runtime_root}</string>".encode()
            + b"""</dict></dict></plist>\n"""
        )
    )

    assert preflight._runtime_state_dir(plist) == runtime_root / "state"


def test_live_trading_launchagent_installed_blocks_missing_active_plist(monkeypatch, tmp_path):
    missing = tmp_path / "com.zeus.live-trading.plist"
    monkeypatch.setattr(preflight, "LIVE_TRADING_PLIST_PATH", missing)

    result = preflight._live_trading_launchagent_installed_check()

    assert result.ok is False
    assert result.name == "live_trading_launchagent_installed"
    assert "missing" in result.detail


def test_live_trading_launchagent_installed_accepts_src_main_plist(monkeypatch, tmp_path):
    plist = tmp_path / "com.zeus.live-trading.plist"
    plist.write_bytes(
        b"""<?xml version="1.0" encoding="UTF-8"?>\n"""
        b"""<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" """
        b""""http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n"""
        b"""<plist version="1.0"><dict>"""
        b"""<key>Label</key><string>com.zeus.live-trading</string>"""
        b"""<key>ProgramArguments</key><array>"""
        b"""<string>/usr/bin/python3</string><string>-m</string><string>src.main</string>"""
        b"""</array></dict></plist>\n"""
    )
    monkeypatch.setattr(preflight, "LIVE_TRADING_PLIST_PATH", plist)

    result = preflight._live_trading_launchagent_installed_check()

    assert result.ok is True


def test_live_trading_launchagent_bootstrapable_blocks_disabled_launchd_service(monkeypatch):
    def _fake_run(command, **kwargs):
        assert command[:2] == ["launchctl", "print-disabled"]
        return subprocess.CompletedProcess(
            command,
            0,
            '"com.zeus.live-trading" => disabled\n',
            "",
        )

    monkeypatch.setattr(preflight.subprocess, "run", _fake_run)

    result = preflight._live_trading_launchagent_bootstrapable_check()

    assert result.ok is False
    assert result.name == "live_trading_launchagent_bootstrapable"
    assert "disabled" in result.detail
    assert result.evidence["disabled_value"] == "disabled"


def test_preflight_blocks_when_live_launchagent_plist_exists_but_is_disabled(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=fresh)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()
    monkeypatch.setattr(
        preflight,
        "_live_trading_launchagent_bootstrapable_check",
        lambda: preflight.CheckResult(
            "live_trading_launchagent_bootstrapable",
            False,
            "active live-trading LaunchAgent is disabled or cannot be inspected",
            {"disabled_value": "disabled"},
        ),
    )

    result = preflight.evaluate()

    assert result["ok"] is False
    bootstrapable = next(c for c in result["checks"] if c["name"] == "live_trading_launchagent_bootstrapable")
    assert bootstrapable["ok"] is False


def _write_live_plist_with_env(path: Path, env: dict[str, str]) -> None:
    env_xml = b"".join(
        f"<key>{key}</key><string>{value}</string>".encode()
        for key, value in sorted(env.items())
    )
    path.write_bytes(
        b"""<?xml version="1.0" encoding="UTF-8"?>\n"""
        b"""<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" """
        b""""http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n"""
        b"""<plist version="1.0"><dict>"""
        b"""<key>Label</key><string>com.zeus.live-trading</string>"""
        b"""<key>ProgramArguments</key><array>"""
        b"""<string>/usr/bin/python3</string><string>-m</string><string>src.main</string>"""
        b"""</array><key>EnvironmentVariables</key><dict>"""
        + env_xml
        + b"""</dict></dict></plist>\n"""
    )


def test_clob_signature_type_config_blocks_missing_when_submit_armed(monkeypatch, tmp_path):
    plist = tmp_path / "com.zeus.live-trading.plist"
    _write_live_plist_with_env(plist, {})
    monkeypatch.setattr(preflight, "LIVE_TRADING_PLIST_PATH", plist)
    monkeypatch.setattr(preflight, "CLOB_SIGNATURE_TYPE_SIDECAR_LABELS", ())

    result = preflight._clob_signature_type_config_check(required=True)

    assert result.ok is False
    assert result.name == "clob_signature_type_config"
    assert result.evidence["present"] is False
    assert "POLYMARKET_CLOB_V2_SIGNATURE_TYPE" in result.detail


def test_clob_signature_type_config_blocks_unsupported_value(monkeypatch, tmp_path):
    plist = tmp_path / "com.zeus.live-trading.plist"
    _write_live_plist_with_env(plist, {"POLYMARKET_CLOB_V2_SIGNATURE_TYPE": "9"})
    monkeypatch.setattr(preflight, "LIVE_TRADING_PLIST_PATH", plist)
    monkeypatch.setattr(preflight, "CLOB_SIGNATURE_TYPE_SIDECAR_LABELS", ())

    result = preflight._clob_signature_type_config_check(required=True)

    assert result.ok is False
    assert result.evidence["configured_value"] == "9"
    assert "unsupported" in result.detail


def test_clob_signature_type_config_accepts_explicit_supported_value(monkeypatch, tmp_path):
    plist = tmp_path / "com.zeus.live-trading.plist"
    _write_live_plist_with_env(plist, {"POLYMARKET_CLOB_V2_SIGNATURE_TYPE": "2"})
    monkeypatch.setattr(preflight, "LIVE_TRADING_PLIST_PATH", plist)
    monkeypatch.setattr(preflight, "CLOB_SIGNATURE_TYPE_SIDECAR_LABELS", ())

    result = preflight._clob_signature_type_config_check(required=True)

    assert result.ok is True
    assert result.evidence["configured_value"] == "2"


def test_clob_signature_type_config_blocks_missing_sidecar_value(monkeypatch, tmp_path):
    live = tmp_path / "com.zeus.live-trading.plist"
    price = tmp_path / "com.zeus.price-channel-ingest.plist"
    venue = tmp_path / "com.zeus.venue-heartbeat.plist"
    _write_live_plist_with_env(live, {"POLYMARKET_CLOB_V2_SIGNATURE_TYPE": "2"})
    _write_live_plist_with_env(price, {"POLYMARKET_CLOB_V2_SIGNATURE_TYPE": "2"})
    _write_live_plist_with_env(venue, {})
    monkeypatch.setattr(preflight, "LIVE_TRADING_PLIST_PATH", live)
    monkeypatch.setattr(
        preflight,
        "CLOB_SIGNATURE_TYPE_SIDECAR_LABELS",
        ("price-channel-ingest", "venue-heartbeat"),
    )
    paths = {
        "live-trading": live,
        "price-channel-ingest": price,
        "venue-heartbeat": venue,
    }
    monkeypatch.setattr(preflight, "_launchagent_plist_path_for_label", lambda label: paths[label])

    result = preflight._clob_signature_type_config_check(required=True)

    assert result.ok is False
    assert "venue-heartbeat" in result.detail
    assert any(
        item["label"] == "venue-heartbeat" and item["present"] is False
        for item in result.evidence["items"]
    )


def test_harvester_live_enabled_uses_live_plist_not_shell_env(monkeypatch, tmp_path):
    plist = tmp_path / "com.zeus.live-trading.plist"
    _write_live_plist_with_env(plist, {"ZEUS_HARVESTER_LIVE_ENABLED": "1"})
    monkeypatch.setattr(preflight, "LIVE_TRADING_PLIST_PATH", plist)
    monkeypatch.setenv("ZEUS_HARVESTER_LIVE_ENABLED", "0")

    enabled, evidence = preflight._harvester_live_enabled()

    assert enabled is True
    assert evidence["source"] == "live_trading_launchagent_plist"
    assert evidence["shell_env_value_ignored"] == "0"
    assert evidence["plist_value"] == "1"


def test_import_time_db_paths_follow_live_plist_primary_root(tmp_path):
    home = tmp_path / "home"
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    runtime_root = tmp_path / "runtime-root"
    plist = launch_agents / "com.zeus.live-trading.plist"
    plist.write_bytes(
        (
            b"""<?xml version="1.0" encoding="UTF-8"?>\n"""
            b"""<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" """
            b""""http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n"""
            b"""<plist version="1.0"><dict><key>EnvironmentVariables</key><dict>"""
            + f"<key>ZEUS_PRIMARY_ROOT</key><string>{runtime_root}</string>".encode()
            + b"""</dict></dict></plist>\n"""
        )
    )
    env = os.environ.copy()
    for key in (
        "ZEUS_LIVE_PREFLIGHT_STATE_DIR",
        "ZEUS_STATE_DIR",
        "ZEUS_PRIMARY_ROOT",
        "ZEUS_TRADE_DB",
        "ZEUS_WORLD_DB",
        "ZEUS_FORECAST_DB",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(preflight.ROOT)
    code = """
import json
from scripts import check_live_restart_preflight as p
print(json.dumps({
    "state": str(p.STATE_DIR),
    "trade": str(p.TRADE_DB),
    "world": str(p.WORLD_DB),
    "forecast": str(p.FORECAST_DB),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=preflight.ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    state_dir = runtime_root.resolve() / "state"

    assert payload == {
        "state": str(state_dir),
        "trade": str(state_dir / "zeus_trades.db"),
        "world": str(state_dir / "zeus-world.db"),
        "forecast": str(state_dir / "zeus-forecasts.db"),
    }


def test_preflight_blocks_qkernel_cutover_flag_off(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=fresh)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()
    preflight.SETTINGS_PATH.write_text(
        json.dumps(
            {
                "edli": {"real_order_submit_enabled": True},
                "feature_flags": {"qkernel_spine_enabled": False},
            }
        )
    )

    result = preflight.evaluate()

    assert result["ok"] is False
    qkernel = next(c for c in result["checks"] if c["name"] == "qkernel_spine_cutover")
    assert qkernel["ok"] is False


def test_preflight_qlcb_check_uses_preflight_state_dir(monkeypatch, tmp_path):
    _trade_db, _forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(guard_mod, "_QLCB_OOF_RELIABILITY_PATH", str(tmp_path / "wrong.json"))
    guard_mod.reset_reliability_cache()

    result = preflight._qlcb_reliability_artifact_check()

    assert result.ok is True
    assert result.evidence["status"] == "ACTIVE_VALID"
    assert result.evidence["path"] == str(state_dir / "qlcb_oof_reliability.json")


def test_preflight_blocks_live_family_portfolio_max_legs_gt_one(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=fresh)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    monkeypatch.setenv("ZEUS_LIVE_FAMILY_PORTFOLIO_MAX_LEGS", "2")
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()

    result = preflight.evaluate()

    assert result["ok"] is False
    max_legs = next(c for c in result["checks"] if c["name"] == "family_portfolio_single_leg_cutover")
    assert max_legs["ok"] is False
    assert max_legs["evidence"]["effective_max_legs"] == 2


def test_preflight_blocks_absent_qlcb_artifact(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=fresh)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    (state_dir / "qlcb_oof_reliability.json").unlink()
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()

    result = preflight.evaluate()

    assert result["ok"] is False
    qlcb = next(c for c in result["checks"] if c["name"] == "qlcb_reliability_artifact")
    assert qlcb["ok"] is False
    assert qlcb["evidence"]["status"] == "ABSENT_ALLOWED"


def test_preflight_blocks_present_invalid_qlcb_artifact(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=fresh)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    (state_dir / "qlcb_oof_reliability.json").write_text("{not-json")
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()

    result = preflight.evaluate()

    assert result["ok"] is False
    qlcb = next(c for c in result["checks"] if c["name"] == "qlcb_reliability_artifact")
    assert qlcb["ok"] is False
    assert qlcb["evidence"]["status"] == "ACTIVE_INVALID"


def test_preflight_blocks_shape_valid_stale_qlcb_artifact(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=fresh)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    (state_dir / "qlcb_oof_reliability.json").write_text(
        json.dumps(
            {
                "meta": {
                    "schema_version": guard_mod.EXPECTED_SCHEMA_VERSION,
                    "source": "/tmp/multilead_forecasts.json previous-runs corpus",
                },
                "cells": {
                    "high|L1|YES|modal|qb1|coarse_global": {"n": 100, "hit_rate": 0.80},
                },
            }
        )
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()

    result = preflight.evaluate()

    assert result["ok"] is False
    qlcb = next(c for c in result["checks"] if c["name"] == "qlcb_reliability_artifact")
    assert qlcb["ok"] is False
    assert qlcb["evidence"]["status"] == "STALE_SEMANTICS"


def _init_sidecar_surfaces(conn, *, now: datetime):
    conn.execute(
        """
        CREATE TABLE execution_feasibility_evidence (
            quote_seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO execution_feasibility_evidence VALUES (?)",
        (now.isoformat(),),
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?, ?)",
        (now.isoformat(), (now + timedelta(minutes=2)).isoformat()),
    )
    _insert_collateral_snapshot(conn, now=now)
    conn.commit()


def _insert_collateral_snapshot(conn, *, now: datetime):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collateral_ledger_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            authority_tier TEXT NOT NULL,
            pusd_balance_micro INTEGER,
            pusd_allowance_micro INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO collateral_ledger_snapshots (
            captured_at, authority_tier, pusd_balance_micro, pusd_allowance_micro
        ) VALUES (?, 'CHAIN', 1000000, 1000000)
        """,
        (now.isoformat(),),
    )


def _write_fresh_sidecar_heartbeats(state_dir, *, now: datetime):
    for _, filename in preflight.SIDECAR_HEARTBEATS:
        (state_dir / filename).write_text(
            json.dumps({"alive_at": now.isoformat(), "pid": 123, "git_head": "testsha"})
        )


def _add_identity_columns(trade):
    trade.execute("ALTER TABLE position_current ADD COLUMN condition_id TEXT")
    trade.execute("ALTER TABLE position_current ADD COLUMN token_id TEXT")
    trade.execute("ALTER TABLE position_current ADD COLUMN no_token_id TEXT")


def _init_sidecar_surfaces_for_identity(
    trade,
    *,
    now: datetime,
    condition_id: str,
    yes_token_id: str,
    no_token_id: str,
):
    trade.execute(
        """
        CREATE TABLE execution_feasibility_evidence (
            condition_id TEXT,
            token_id TEXT,
            quote_seen_at TEXT NOT NULL
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE executable_market_snapshots (
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            selected_outcome_token_id TEXT,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL
        )
        """
    )
    trade.execute(
        "INSERT INTO execution_feasibility_evidence VALUES (?, ?, ?)",
        (condition_id, no_token_id, now.isoformat()),
    )
    trade.execute(
        """
        INSERT INTO executable_market_snapshots VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            condition_id,
            yes_token_id,
            no_token_id,
            no_token_id,
            now.isoformat(),
            (now + timedelta(minutes=2)).isoformat(),
        ),
    )
    _insert_collateral_snapshot(trade, now=now)


def test_preflight_blocks_unhealthy_replacement_forecast_sidecar(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    _init_trade_db(trade_db).close()
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    forecasts.commit()
    forecasts.close()
    preflight.SCHEDULER_HEALTH_PATH.write_text(
        json.dumps(
            {
                "bayes_precision_fusion_capture": {
                    "status": "OK",
                    "last_run_at": fresh.isoformat(),
                    "last_success_at": fresh.isoformat(),
                },
                "replacement_forecast_download": {
                    "status": "OK",
                    "last_run_at": fresh.isoformat(),
                    "last_success_at": fresh.isoformat(),
                },
                "replacement_forecast_live_materialize": {
                    "status": "OK",
                    "last_run_at": (fresh + timedelta(seconds=1)).isoformat(),
                    "last_success_at": fresh.isoformat(),
                    "last_failure_at": (fresh + timedelta(seconds=1)).isoformat(),
                    "last_failure_reason": "no such column: p.trade_authority_status",
                },
            }
        )
    )

    result = preflight.evaluate()

    assert result["ok"] is False
    sidecar = next(c for c in result["checks"] if c["name"] == "forecast_sidecar_health")
    assert sidecar["ok"] is False
    assert sidecar["evidence"]["risky"][0]["risk"] == "latest_scheduler_outcome_failed"
    assert "trade_authority_status" in sidecar["evidence"]["risky"][0]["last_failure_reason"]


def test_preflight_blocks_unhealthy_bpf_capture_scheduler_job(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    _init_trade_db(trade_db).close()
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    forecasts.commit()
    forecasts.close()
    health = json.loads(preflight.SCHEDULER_HEALTH_PATH.read_text())
    health["bayes_precision_fusion_capture"] = {
        "status": "FAILED",
        "last_run_at": fresh.isoformat(),
        "last_failure_at": fresh.isoformat(),
        "last_failure_reason": "global_models_unavailable",
    }
    preflight.SCHEDULER_HEALTH_PATH.write_text(json.dumps(health))

    result = preflight.evaluate()

    assert result["ok"] is False
    sidecar = next(c for c in result["checks"] if c["name"] == "forecast_sidecar_health")
    assert sidecar["ok"] is False
    assert sidecar["evidence"]["risky"][0]["job"] == "bayes_precision_fusion_capture"
    assert sidecar["evidence"]["risky"][0]["risk"] == "scheduler_job_failed"


def test_preflight_allows_bpf_capture_transport_degraded_skip(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=fresh)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    trade.close()
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    forecasts.commit()
    forecasts.close()
    health = json.loads(preflight.SCHEDULER_HEALTH_PATH.read_text())
    health["bayes_precision_fusion_capture"] = {
        "status": "SKIPPED",
        "last_run_at": fresh.isoformat(),
        "last_skip_at": fresh.isoformat(),
        "last_skip_reason": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
        "last_success_at": (fresh - timedelta(hours=6)).isoformat(),
        "business_liveness": {
            "transport_degraded": True,
            "transport_degradation_reason": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "quota_cooldown_seconds": 0,
        },
    }
    preflight.SCHEDULER_HEALTH_PATH.write_text(json.dumps(health))

    result = preflight.evaluate()

    assert result["ok"] is True
    sidecar = next(c for c in result["checks"] if c["name"] == "forecast_sidecar_health")
    assert sidecar["ok"] is True
    bpf = sidecar["evidence"]["jobs"]["bayes_precision_fusion_capture"]
    assert bpf["status"] == "SKIPPED"
    assert bpf["business_liveness"]["transport_degraded"] is True
    assert sidecar["evidence"]["risky"] == []


def test_preflight_blocks_forecast_live_heartbeat_missing_replacement_jobs(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    _init_trade_db(trade_db).close()
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    forecasts.commit()
    forecasts.close()
    heartbeat = json.loads(preflight.FORECAST_LIVE_HEARTBEAT_PATH.read_text())
    heartbeat["jobs"] = ["forecast_live_heartbeat"]
    preflight.FORECAST_LIVE_HEARTBEAT_PATH.write_text(json.dumps(heartbeat))

    result = preflight.evaluate()

    assert result["ok"] is False
    sidecar = next(c for c in result["checks"] if c["name"] == "forecast_sidecar_health")
    assert sidecar["ok"] is False
    risks = {item["risk"] for item in sidecar["evidence"]["risky"]}
    assert "forecast_live_heartbeat_missing_replacement_jobs" in risks


def test_preflight_accepts_fresh_running_replacement_forecast_sidecar_job(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=fresh)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    trade.close()
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    forecasts.commit()
    forecasts.close()
    health = json.loads(preflight.SCHEDULER_HEALTH_PATH.read_text())
    health["replacement_forecast_download"] = {
        "status": "RUNNING",
        "last_run_at": fresh.isoformat(),
        "last_started_at": fresh.isoformat(),
    }
    preflight.SCHEDULER_HEALTH_PATH.write_text(json.dumps(health))

    result = preflight.evaluate()

    assert result["ok"] is True
    sidecar = next(c for c in result["checks"] if c["name"] == "forecast_sidecar_health")
    assert sidecar["ok"] is True
    assert sidecar["evidence"]["risky"] == []


def test_preflight_blocks_stale_running_replacement_forecast_sidecar_job(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=fresh)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    trade.close()
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    forecasts.commit()
    forecasts.close()
    stale_started = fresh - timedelta(
        seconds=preflight.REPLACEMENT_SIDECAR_RUNNING_MAX_AGE_SECONDS + 60
    )
    health = json.loads(preflight.SCHEDULER_HEALTH_PATH.read_text())
    health["replacement_forecast_download"] = {
        "status": "RUNNING",
        "last_run_at": stale_started.isoformat(),
        "last_started_at": stale_started.isoformat(),
    }
    preflight.SCHEDULER_HEALTH_PATH.write_text(json.dumps(health))

    result = preflight.evaluate()

    assert result["ok"] is False
    sidecar = next(c for c in result["checks"] if c["name"] == "forecast_sidecar_health")
    assert sidecar["ok"] is False
    risks = {item["risk"] for item in sidecar["evidence"]["risky"]}
    assert "scheduler_job_running_stale" in risks


def test_preflight_blocks_dust_projection_that_would_reload_as_pending_exit(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    _init_forecast_db(forecast_db).close()
    trade.execute(
        """
        INSERT INTO position_current VALUES (
            'dust-pos', 'pending_exit', 'Qingdao', '2026-06-19', 'high',
            'Will the highest temperature in Qingdao be 24°C on June 19?',
            'buy_no', 0.01, 0.01, 'filled', 'EXIT_CHAIN_DUST_STILL_HELD',
            7, NULL, 0.54, 0, 0.73, 0, '2026-06-18T11:14:04+00:00'
        )
        """
    )
    trade.commit()
    trade.close()

    result = preflight.evaluate()

    assert result["ok"] is False
    pending = next(c for c in result["checks"] if c["name"] == "pending_exit_restart_risk")
    assert pending["ok"] is False
    assert pending["evidence"]["risky"][0]["risk"] == "dust_projection_needs_backoff_exhausted_reload_repair"


def test_preflight_tolerates_pending_exit_with_full_exit_fill_repair_evidence(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    _init_forecast_db(forecast_db).close()
    trade.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            intent_kind TEXT,
            state TEXT,
            venue_order_id TEXT,
            size REAL,
            updated_at TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE venue_trade_facts (
            command_id TEXT,
            state TEXT,
            filled_size TEXT,
            fill_price TEXT,
            observed_at TEXT
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_current VALUES (
            'exit-filled-pos', 'pending_exit', 'Seoul', '2026-06-26', 'low',
            'Will the lowest temperature in Seoul be 18°C on June 26?',
            'buy_no', 15.5, 15.5, 'backoff_exhausted', 'FAMILY_DIRECT_SELL_DOMINATES_HOLD',
            19, '2026-06-24T17:40:08+00:00', 0.60, 1, 0.70, 1,
            '2026-06-24T17:45:21+00:00'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO venue_commands VALUES (
            'cmd-exit', 'exit-filled-pos', 'EXIT', 'FILLED', 'ord-exit', 15.5,
            '2026-06-24T15:34:59+00:00'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO venue_trade_facts VALUES (
            'cmd-exit', 'MATCHED', '15.5', '0.70', '2026-06-24T15:34:59+00:00'
        )
        """
    )
    trade.commit()
    trade.close()

    result = preflight.evaluate()

    pending = next(c for c in result["checks"] if c["name"] == "pending_exit_restart_risk")
    assert pending["ok"] is True
    tolerated = pending["evidence"]["tolerated"][0]
    assert tolerated["restart_resolution"] == "command_recovery_full_exit_fill_close"
    assert tolerated["repair_evidence"]["filled_size"] == 15.5


def test_preflight_tolerates_pending_exit_with_full_exit_fill_plus_dust_repair_evidence(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    _init_forecast_db(forecast_db).close()
    trade.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            intent_kind TEXT,
            state TEXT,
            venue_order_id TEXT,
            size REAL,
            updated_at TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE venue_trade_facts (
            command_id TEXT,
            state TEXT,
            filled_size TEXT,
            fill_price TEXT,
            observed_at TEXT
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_current VALUES (
            'exit-filled-dust-pos', 'pending_exit', 'Kuala Lumpur', '2026-07-02', 'high',
            'Will the highest temperature in Kuala Lumpur be 34°C on July 2?',
            'buy_yes', 10.0102, 10.0102, 'sell_placed',
            'DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES',
            2, '2026-07-02T00:09:17+00:00', 0.0, 1, 0.009, 1,
            '2026-07-02T00:16:16+00:00'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO venue_commands VALUES (
            'cmd-exit-dust', 'exit-filled-dust-pos', 'EXIT', 'FILLED',
            'ord-exit-dust', 10.01, '2026-07-02T00:10:29+00:00'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO venue_trade_facts VALUES (
            'cmd-exit-dust', 'MATCHED', '10.01', '0.009',
            '2026-07-02T00:10:29+00:00'
        )
        """
    )
    trade.commit()
    trade.close()

    result = preflight.evaluate()

    pending = next(c for c in result["checks"] if c["name"] == "pending_exit_restart_risk")
    assert pending["ok"] is True
    tolerated = pending["evidence"]["tolerated"][0]
    assert tolerated["restart_resolution"] == "command_recovery_full_exit_fill_close"
    repair = tolerated["repair_evidence"]
    assert repair["filled_size"] == 10.01
    assert repair["residual_is_dust"] is True
    assert 0.0 < repair["residual_shares"] <= preflight.DUST_SHARE_LIMIT


def _init_confirmed_fill_bridge_gap_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL,
            position_id TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_trade_facts (
            command_id TEXT NOT NULL,
            venue_order_id TEXT NOT NULL,
            trade_id TEXT NOT NULL,
            source TEXT NOT NULL,
            state TEXT NOT NULL,
            filled_size TEXT NOT NULL,
            fill_price TEXT NOT NULL,
            observed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events VALUES (
            'agg-1', 'ExecutionCommandCreated', ?, '2026-06-29T20:00:00+00:00'
        )
        """,
        (
            json.dumps(
                {
                    "event_id": "event-1",
                    "final_intent_id": "intent-1",
                    "execution_command_id": "exec-1",
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events VALUES (
            'agg-1', 'VenueSubmitAcknowledged', ?, '2026-06-29T20:00:05+00:00'
        )
        """,
        (json.dumps({"venue_order_id": "ord-1"}),),
    )
    conn.execute("INSERT INTO venue_commands VALUES ('cmd-1', 'exec-1', 'pos-1')")
    conn.execute(
        """
        INSERT INTO venue_trade_facts VALUES (
            'cmd-1', 'ord-1', 'trade-1', 'WS_USER', 'CONFIRMED',
            '10.5', '0.54', '2026-06-29T20:00:10+00:00'
        )
        """
    )
    conn.commit()
    return conn


def test_confirmed_fill_bridge_coverage_blocks_unbridged_ws_confirmed_fill(
    monkeypatch,
    tmp_path,
):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    _init_forecast_db(forecast_db).close()
    conn = _init_confirmed_fill_bridge_gap_db(trade_db)
    conn.close()

    check = preflight._edli_confirmed_fill_bridge_coverage_check()

    assert check.ok is False
    assert check.name == "edli_confirmed_fill_bridge_coverage"
    assert check.evidence["missing_confirmed_fill_count"] == 1
    assert check.evidence["samples"][0]["trade_id"] == "trade-1"
    assert check.evidence["samples"][0]["command_id"] == "cmd-1"


def test_confirmed_fill_bridge_coverage_accepts_already_bridged_trade(
    monkeypatch,
    tmp_path,
):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    _init_forecast_db(forecast_db).close()
    conn = _init_confirmed_fill_bridge_gap_db(trade_db)
    conn.execute(
        """
        INSERT INTO edli_live_order_events VALUES (
            'agg-1', 'UserTradeObserved', ?, '2026-06-29T20:00:11+00:00'
        )
        """,
        (
            json.dumps(
                {
                    "trade_id": "trade-1",
                    "fill_authority_state": "FILL_CONFIRMED",
                }
            ),
        ),
    )
    conn.commit()
    conn.close()

    check = preflight._edli_confirmed_fill_bridge_coverage_check()

    assert check.ok is True
    assert check.evidence["missing_confirmed_fill_count"] == 0


def test_preflight_tolerates_retry_pending_without_resting_exit_order(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    _init_forecast_db(forecast_db).close()
    trade.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            intent_kind TEXT,
            state TEXT,
            venue_order_id TEXT,
            size REAL,
            updated_at TEXT
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_current VALUES (
            'retry-pos', 'pending_exit', 'Houston', '2026-06-24', 'high',
            'Will the highest temperature in Houston be between 92-93°F on June 24?',
            'buy_no', 36.0, 36.0, 'filled', 'CI_SEPARATED_REVERSAL',
            4, '2026-06-24T18:22:42+00:00', 0.055, 1, 0.53, 1,
            '2026-06-24T17:42:42+00:00'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO venue_commands VALUES (
            'cmd-exit', 'retry-pos', 'EXIT', 'REJECTED', '', 36.0,
            '2026-06-24T17:42:42+00:00'
        )
        """
    )
    trade.commit()
    trade.close()

    result = preflight.evaluate()

    pending = next(c for c in result["checks"] if c["name"] == "pending_exit_restart_risk")
    assert pending["ok"] is True
    tolerated = pending["evidence"]["tolerated"][0]
    assert tolerated["restart_resolution"] == "exit_lifecycle_retry_resume"
    assert tolerated["repair_evidence"]["command_state"] == "REJECTED"


def test_preflight_tolerates_pre_submit_exit_retry_without_exit_command(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    _init_forecast_db(forecast_db).close()
    trade.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            intent_kind TEXT,
            state TEXT,
            venue_order_id TEXT,
            size REAL,
            updated_at TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT,
            sequence_no INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            venue_status TEXT,
            payload_json TEXT
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_current VALUES (
            'retry-no-command-pos', 'pending_exit', 'Singapore', '2026-06-26', 'high',
            'Will the highest temperature in Singapore be 30°C on June 26?',
            'buy_yes', 1.031967, 1.0319, 'filled', 'DAY0_HARD_FACT_BIN_DEAD',
            9, '2026-06-26T10:58:15+00:00', 0.0, 1, 0.031, 1,
            '2026-06-26T10:06:50+00:00'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_events VALUES (
            'retry-no-command-pos:phase_transition:505',
            'retry-no-command-pos',
            505,
            'EXIT_ORDER_REJECTED',
            '2026-06-26T09:58:15+00:00',
            'retry_pending',
            '{"error":"executable_snapshot_gate: venue command requires executable market snapshot_id"}'
        )
        """
    )
    trade.commit()
    trade.close()

    result = preflight.evaluate()

    pending = next(c for c in result["checks"] if c["name"] == "pending_exit_restart_risk")
    assert pending["ok"] is True
    tolerated = pending["evidence"]["tolerated"][0]
    assert tolerated["restart_resolution"] == "exit_lifecycle_pre_submit_retry_resume"
    assert tolerated["repair_evidence"]["command_state"] == "NO_EXIT_COMMAND_RETRY_PENDING"
    assert tolerated["repair_evidence"]["event_type"] == "EXIT_ORDER_REJECTED"


def test_preflight_tolerates_pending_exit_phantom_sell_projection(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    _init_forecast_db(forecast_db).close()
    trade.execute("ALTER TABLE position_current ADD COLUMN order_id TEXT")
    trade.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            intent_kind TEXT,
            state TEXT,
            venue_order_id TEXT,
            size REAL,
            updated_at TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE venue_order_facts (
            venue_order_id TEXT,
            command_id TEXT,
            state TEXT,
            matched_size TEXT,
            remaining_size TEXT,
            observed_at TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE venue_trade_facts (
            venue_order_id TEXT,
            command_id TEXT,
            state TEXT,
            filled_size TEXT,
            fill_price TEXT,
            observed_at TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT,
            sequence_no INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            venue_status TEXT,
            payload_json TEXT
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            bin_label, direction, shares, chain_shares, order_status, exit_reason,
            exit_retry_count, next_exit_retry_at, last_monitor_prob,
            last_monitor_prob_is_fresh, last_monitor_market_price,
            last_monitor_market_price_is_fresh, updated_at, order_id
        ) VALUES (
            'phantom-exit-pos', 'pending_exit', 'Miami', '2026-06-30', 'high',
            '96-97F', 'buy_yes', 85.17, 85.17, 'sell_placed',
            'ENTRY_SELECTION_GUARD_INVALID_EXIT', 2, NULL, 0.09, 1, 0.05, 1,
            '2026-06-29T19:22:48+00:00',
            '0xphantomexit'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO venue_commands VALUES (
            'entry-cmd', 'phantom-exit-pos', 'ENTRY', 'CANCELLED', '0xentry',
            85.17, '2026-06-29T11:18:50+00:00'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_events VALUES (
            'phantom-exit-pos:monitor_refreshed:1',
            'phantom-exit-pos',
            1,
            'MONITOR_REFRESHED',
            '2026-06-29T19:19:48+00:00',
            'sell_placed',
            '{}'
        )
        """
    )
    trade.commit()
    trade.close()

    result = preflight.evaluate()

    pending = next(c for c in result["checks"] if c["name"] == "pending_exit_restart_risk")
    assert pending["ok"] is True
    tolerated = pending["evidence"]["tolerated"][0]
    assert tolerated["restart_resolution"] == "exit_lifecycle_pending_exit_no_order_release"
    assert tolerated["repair_evidence"]["projected_order_id"] == "0xphantomexit"
    assert tolerated["repair_evidence"]["exit_command_count"] == 0
    assert tolerated["repair_evidence"]["venue_order_fact_count"] == 0
    assert tolerated["repair_evidence"]["venue_trade_fact_count"] == 0


def test_preflight_tolerates_retrying_pending_exit_posted_without_venue_truth(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    _init_forecast_db(forecast_db).close()
    trade.execute("ALTER TABLE position_current ADD COLUMN order_id TEXT")
    trade.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            intent_kind TEXT,
            state TEXT,
            venue_order_id TEXT,
            size REAL,
            updated_at TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE venue_order_facts (
            venue_order_id TEXT,
            command_id TEXT,
            state TEXT,
            matched_size TEXT,
            remaining_size TEXT,
            observed_at TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE venue_trade_facts (
            venue_order_id TEXT,
            command_id TEXT,
            state TEXT,
            filled_size TEXT,
            fill_price TEXT,
            observed_at TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT,
            sequence_no INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            venue_status TEXT,
            payload_json TEXT
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            bin_label, direction, shares, chain_shares, order_status, exit_reason,
            exit_retry_count, next_exit_retry_at, last_monitor_prob,
            last_monitor_prob_is_fresh, last_monitor_market_price,
            last_monitor_market_price_is_fresh, updated_at, order_id
        ) VALUES (
            'stale-posted-exit-pos', 'pending_exit', 'Miami', '2026-06-30', 'high',
            '96-97F', 'buy_yes', 85.17, 85.17, 'sell_placed',
            'ENTRY_SELECTION_GUARD_INVALID_EXIT', 2, NULL, 0.09, 1, 0.05, 1,
            '2026-06-29T19:22:48+00:00',
            '0xstaleexit'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_events VALUES (
            'stale-posted-exit-pos:exit_order_posted:1',
            'stale-posted-exit-pos',
            1,
            'EXIT_ORDER_POSTED',
            '2026-06-29T18:09:53+00:00',
            'sell_pending',
            '{}'
        )
        """
    )
    trade.commit()
    trade.close()

    result = preflight.evaluate()

    pending = next(c for c in result["checks"] if c["name"] == "pending_exit_restart_risk")
    assert pending["ok"] is True
    tolerated = pending["evidence"]["tolerated"][0]
    assert tolerated["restart_resolution"] == "exit_lifecycle_pending_exit_no_order_release"
    assert tolerated["repair_evidence"]["projected_order_id"] == "0xstaleexit"
    assert tolerated["repair_evidence"]["exit_order_posted_count"] == 1
    assert tolerated["repair_evidence"]["exit_retry_count"] == 2


def test_preflight_tolerates_pending_exit_with_monitorable_active_exit_command(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    _init_forecast_db(forecast_db).close()
    trade.execute("ALTER TABLE position_current ADD COLUMN order_id TEXT")
    trade.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            intent_kind TEXT,
            side TEXT,
            state TEXT,
            venue_order_id TEXT,
            size REAL,
            price REAL,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            bin_label, direction, shares, chain_shares, order_status, exit_reason,
            exit_retry_count, next_exit_retry_at, last_monitor_prob,
            last_monitor_prob_is_fresh, last_monitor_market_price,
            last_monitor_market_price_is_fresh, updated_at, order_id
        ) VALUES (
            'active-exit-pos', 'pending_exit', 'Miami', '2026-06-30', 'high',
            '96-97F', 'buy_yes', 85.17, 85.17, 'sell_placed',
            'ENTRY_SELECTION_GUARD_INVALID_EXIT', 3, NULL, 0.09, 1, 0.05, 1,
            '2026-06-29T19:47:04+00:00',
            '0xactiveexit'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO venue_commands VALUES (
            'cmd-active-exit', 'active-exit-pos', 'EXIT', 'SELL', 'ACKED',
            '0xactiveexit', 85.17, 0.054,
            '2026-06-29T19:47:04+00:00',
            '2026-06-29T19:47:04+00:00'
        )
        """
    )
    trade.commit()
    trade.close()

    result = preflight.evaluate()

    pending = next(c for c in result["checks"] if c["name"] == "pending_exit_restart_risk")
    assert pending["ok"] is True
    tolerated = pending["evidence"]["tolerated"][0]
    assert tolerated["restart_resolution"] == "exit_lifecycle_active_exit_command_monitor"
    assert tolerated["repair_evidence"]["command_id"] == "cmd-active-exit"
    assert tolerated["repair_evidence"]["venue_order_id"] == "0xactiveexit"


def test_preflight_blocks_pending_exit_with_real_exit_command(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    _init_forecast_db(forecast_db).close()
    trade.execute("ALTER TABLE position_current ADD COLUMN order_id TEXT")
    trade.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            intent_kind TEXT,
            state TEXT,
            venue_order_id TEXT,
            size REAL,
            updated_at TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT,
            sequence_no INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            venue_status TEXT,
            payload_json TEXT
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            bin_label, direction, shares, chain_shares, order_status, exit_reason,
            exit_retry_count, next_exit_retry_at, last_monitor_prob,
            last_monitor_prob_is_fresh, last_monitor_market_price,
            last_monitor_market_price_is_fresh, updated_at, order_id
        ) VALUES (
            'real-exit-pos', 'pending_exit', 'Miami', '2026-06-30', 'high',
            '96-97F', 'buy_yes', 85.17, 85.17, 'sell_placed',
            'ENTRY_SELECTION_GUARD_INVALID_EXIT', 2, NULL, 0.09, 1, 0.05, 1,
            '2026-06-29T19:22:48+00:00',
            '0xrealexit'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO venue_commands VALUES (
            'exit-cmd', 'real-exit-pos', 'EXIT', 'PLACED', '0xrealexit',
            85.17, '2026-06-29T19:18:50+00:00'
        )
        """
    )
    trade.commit()
    trade.close()

    result = preflight.evaluate()

    pending = next(c for c in result["checks"] if c["name"] == "pending_exit_restart_risk")
    assert pending["ok"] is False
    assert pending["evidence"]["risky"][0]["position_id"] == "real-exit-pos"


def test_preflight_blocks_active_position_with_stale_live_belief(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    label = "Will the highest temperature in Seattle be between 82-83°F on June 19?"
    trade.execute(
        """
        INSERT INTO position_current VALUES (
            'active-pos', 'active', 'Seattle', '2026-06-19', 'high',
            ?, 'buy_no', 9.0, 9.0, 'filled', NULL, 0, NULL,
            0.84, 1, 0.72, 1, '2026-06-18T11:01:17+00:00'
        )
        """,
        (label,),
    )
    stale = datetime.now(timezone.utc) - timedelta(hours=72)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, ?, 'live')
        """,
        (
            stale.isoformat(),
            stale.isoformat(),
            json.dumps({label: 0.15}),
        ),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()
    monkeypatch.setattr(preflight, "_single_family_reseed_repair_evidence", lambda item: None)

    result = preflight.evaluate()

    assert result["ok"] is False
    belief = next(c for c in result["checks"] if c["name"] == "held_position_belief_coverage")
    assert belief["ok"] is False
    assert belief["evidence"]["risky"][0]["risk"] == "stale_live_belief"


def test_non_day0_monitor_projection_does_not_cover_stale_live_belief(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    label = "Will the highest temperature in Seattle be between 82-83°F on June 26?"
    trade.execute(
        """
        INSERT INTO position_current VALUES (
            'active-pos', 'active', 'Seattle', '2026-06-26', 'high',
            ?, 'buy_no', 9.0, 9.0, 'filled', NULL, 0, NULL,
            0.84, 1, 0.72, 1, '2026-06-24T11:01:17+00:00'
        )
        """,
        (label,),
    )
    trade.execute(
        """
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at, payload_json
        ) VALUES (1, 'active-pos', 'MONITOR_REFRESHED', ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            json.dumps(
                {
                    "applied_validations": [
                        "replacement_posterior",
                        "belief_source=forecast_posteriors;basis=source_cycle_time;fresh",
                    ]
                }
            ),
        ),
    )
    stale = datetime.now(timezone.utc) - timedelta(hours=72)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-26', 'high', ?, ?, ?, 'live')
        """,
        (
            stale.isoformat(),
            stale.isoformat(),
            json.dumps({label: 0.15}),
        ),
    )
    trade.commit()
    forecasts.commit()
    trade.row_factory = sqlite3.Row
    rows = trade.execute("SELECT * FROM position_current").fetchall()
    trade.close()
    forecasts.close()
    monkeypatch.setattr(preflight, "_single_family_reseed_repair_evidence", lambda item: None)

    result = preflight._belief_check(rows)

    assert result.ok is False
    assert result.evidence["risky"][0]["risk"] == "stale_live_belief"
    covered = result.evidence["covered"][0]
    assert covered["fresh"] is False
    assert covered["freshness_basis"] != "monitor_projection_readthrough"


def test_active_position_day0_monitor_projection_covers_stale_forecast_belief(monkeypatch, tmp_path):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    label = "Will the highest temperature in Houston be 98°F on June 24?"
    trade.execute(
        """
        INSERT INTO position_current VALUES (
            'active-day0-pos', 'active', 'Houston', '2026-06-24', 'high',
            ?, 'buy_no', 9.0, 9.0, 'filled', NULL, 0, NULL,
            0.9999, 1, 0.998, 1, ?
        )
        """,
        (label, datetime.now(timezone.utc).isoformat()),
    )
    trade.execute(
        """
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at, payload_json
        ) VALUES (1, 'active-day0-pos', 'MONITOR_REFRESHED', ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            json.dumps(
                {
                    "applied_validations": [
                        "belief_source=day0_observation_remaining_window",
                        "day0_observation_remaining_window",
                    ]
                }
            ),
        ),
    )
    stale = datetime.now(timezone.utc) - timedelta(hours=72)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Houston', '2026-06-24', 'high', ?, ?, ?, 'live')
        """,
        (
            stale.isoformat(),
            stale.isoformat(),
            json.dumps({label: 0.15}),
        ),
    )
    trade.commit()
    forecasts.commit()
    trade.row_factory = sqlite3.Row
    rows = trade.execute("SELECT * FROM position_current").fetchall()
    trade.close()
    forecasts.close()

    result = preflight._belief_check(rows)

    assert result.ok is True
    assert result.evidence["risky"] == []
    covered = result.evidence["covered"][0]
    assert covered["position_id"] == "active-day0-pos"
    assert covered["freshness_basis"] == "active_day0_monitor_projection"
    assert covered["monitor_projection"]["source"] == "day0_monitor_observation_authority"


def test_day0_observation_unavailable_replacement_fallback_covers_held_belief(
    monkeypatch, tmp_path
):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    label = "Will the highest temperature in Manila be 32°C on June 29?"
    trade.execute(
        """
        INSERT INTO position_current VALUES (
            'day0-fallback-pos', 'day0_window', 'Manila', '2026-06-29', 'high',
            ?, 'buy_no', 18.14, 18.14, 'filled', NULL, 0, NULL,
            0.91, 1, 0.77, 1, ?
        )
        """,
        (label, datetime.now(timezone.utc).isoformat()),
    )
    trade.execute(
        """
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at, payload_json
        ) VALUES (1, 'day0-fallback-pos', 'MONITOR_REFRESHED', ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            json.dumps(
                {
                    "applied_validations": [
                        "day0_observation_unavailable:replacement_posterior_available_not_exit_authority",
                        "belief_source=forecast_posteriors;basis=source_cycle_time;fresh",
                    ]
                }
            ),
        ),
    )
    stale = datetime.now(timezone.utc) - timedelta(hours=72)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Manila', '2026-06-29', 'high', ?, ?, ?, 'live')
        """,
        (
            stale.isoformat(),
            stale.isoformat(),
            json.dumps({label: 0.09}),
        ),
    )
    trade.commit()
    forecasts.commit()
    trade.row_factory = sqlite3.Row
    rows = trade.execute("SELECT * FROM position_current").fetchall()
    trade.close()
    forecasts.close()

    result = preflight._belief_check(rows)

    assert result.ok is True
    covered = result.evidence["covered"][0]
    assert covered["position_id"] == "day0-fallback-pos"
    assert covered["freshness_basis"] == "day0_monitor_projection"
    assert covered["monitor_projection"]["source"] == "day0_monitor_replacement_fallback"


def test_day0_stale_monitor_projection_uses_fresh_replacement_hold_boot_fallback(
    monkeypatch, tmp_path
):
    trade_db, forecast_db, _state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    label = "Will the highest temperature in Chicago be between 94-95°F on July 2?"
    now = datetime.now(timezone.utc)
    trade.execute(
        """
        INSERT INTO position_current VALUES (
            'day0-stale-monitor-pos', 'day0_window', 'Chicago', '2026-07-02', 'high',
            ?, 'buy_no', 8.51, 8.51, 'filled', NULL, 0, NULL,
            0.81, 0, 0.60, 1, ?
        )
        """,
        (label, now.isoformat()),
    )
    trade.execute(
        """
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    trade.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at, payload_json
        ) VALUES (1, 'day0-stale-monitor-pos', 'MONITOR_REFRESHED', ?, ?)
        """,
        (
            (now - timedelta(hours=2)).isoformat(),
            json.dumps(
                {
                    "applied_validations": [
                        "day0_observation_unavailable:replacement_posterior_available_not_exit_authority",
                        "belief_source=forecast_posteriors;basis=source_cycle_time;fresh",
                    ]
                }
            ),
        ),
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Chicago', '2026-07-02', 'high', ?, ?, ?, 'live')
        """,
        (
            now.isoformat(),
            now.isoformat(),
            json.dumps({label: 0.19}),
        ),
    )
    trade.commit()
    forecasts.commit()
    trade.row_factory = sqlite3.Row
    rows = trade.execute("SELECT * FROM position_current").fetchall()
    trade.close()
    forecasts.close()

    result = preflight._belief_check(rows)

    assert result.ok is True
    covered = result.evidence["covered"][0]
    assert covered["position_id"] == "day0-stale-monitor-pos"
    assert covered["freshness_basis"] == "day0_replacement_hold_boot_fallback"
    assert covered["restart_resolution"] == "boot_hold_until_day0_observation_monitor_refresh"
    assert covered["exit_authority"] is False
    assert covered["held_side_prob"] == 0.81


def test_preflight_allows_stale_belief_repairable_by_restart_reseed(
    monkeypatch, tmp_path
):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    _add_identity_columns(trade)
    _init_sidecar_surfaces_for_identity(
        trade,
        now=fresh,
        condition_id="cond-karachi",
        yes_token_id="tok-karachi-yes",
        no_token_id="tok-karachi-no",
    )
    label = "Will the highest temperature in Karachi be 35°C on June 19?"
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric, bin_label,
            direction, shares, chain_shares, order_status, exit_reason,
            exit_retry_count, next_exit_retry_at, last_monitor_prob,
            last_monitor_prob_is_fresh, last_monitor_market_price,
            last_monitor_market_price_is_fresh, updated_at,
            condition_id, token_id, no_token_id
        ) VALUES (
            'karachi-pos', 'active', 'Karachi', '2026-06-19', 'high',
            ?, 'buy_no', 5.0, 5.0, 'filled', NULL, 0, NULL,
            0.84, 1, 0.72, 1, '2026-06-18T23:00:00+00:00',
            'cond-karachi', 'tok-karachi-yes', 'tok-karachi-no'
        )
        """,
        (label,),
    )
    _insert_monitor_events(trade, position_id="karachi-pos", monitor_at=fresh, chain_at=fresh)
    stale = datetime.now(timezone.utc) - timedelta(hours=72)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Karachi', '2026-06-19', 'high', ?, ?, ?, 'live')
        """,
        (
            stale.isoformat(),
            stale.isoformat(),
            json.dumps({label: 0.20}),
        ),
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (2, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()

    monkeypatch.setattr(
        preflight,
        "_single_family_reseed_repair_evidence",
        lambda item: {
            **item,
            "risk": "stale_live_belief_repairable_by_single_family_reseed",
            "family_materializable_cycle": "2026-06-18T18:00:00+00:00",
            "write_performed": False,
        },
    )

    result = preflight.evaluate()

    assert result["ok"] is True
    belief = next(c for c in result["checks"] if c["name"] == "held_position_belief_coverage")
    assert belief["ok"] is True
    assert belief["evidence"]["risky"] == []
    covered_resolutions = [
        row.get("restart_resolution") for row in belief["evidence"]["covered"]
    ]
    assert "single_family_cycle_advance_reseed_before_monitor_exit" in covered_resolutions
    repair = belief["evidence"]["repairable"][0]
    assert repair["position_id"] == "karachi-pos"
    assert repair["risk"] == "stale_live_belief_repairable_by_single_family_reseed"
    assert repair["posterior_id"] == "1"


def test_preflight_allows_missing_belief_repairable_by_restart_reseed(
    monkeypatch, tmp_path
):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    _add_identity_columns(trade)
    _init_sidecar_surfaces_for_identity(
        trade,
        now=fresh,
        condition_id="cond-sh",
        yes_token_id="tok-sh-yes",
        no_token_id="tok-sh-no",
    )
    label = "Will the highest temperature in Shanghai be 31°C on June 19?"
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric, bin_label,
            direction, shares, chain_shares, order_status, exit_reason,
            exit_retry_count, next_exit_retry_at, last_monitor_prob,
            last_monitor_prob_is_fresh, last_monitor_market_price,
            last_monitor_market_price_is_fresh, updated_at,
            condition_id, token_id, no_token_id
        ) VALUES (
            'sh-pos', 'active', 'Shanghai', '2026-06-19', 'high',
            ?, 'buy_no', 5.0, 5.0, 'filled', NULL, 0, NULL,
            0.84, 0, 0.72, 1, '2026-06-19T01:00:00+00:00',
            'cond-sh', 'tok-sh-yes', 'tok-sh-no'
        )
        """,
        (label,),
    )
    _insert_monitor_events(trade, position_id="sh-pos", monitor_at=fresh, chain_at=fresh)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()

    monkeypatch.setattr(
        preflight,
        "_single_family_reseed_repair_evidence",
        lambda item: {
            **item,
            "risk": "missing_live_belief_repairable_by_single_family_reseed",
            "family_materializable_cycle": "2026-06-18T18:00:00+00:00",
            "write_performed": False,
        },
    )

    result = preflight.evaluate()

    assert result["ok"] is True
    belief = next(c for c in result["checks"] if c["name"] == "held_position_belief_coverage")
    assert belief["ok"] is True
    assert belief["evidence"]["risky"] == []
    covered_resolutions = [
        row.get("restart_resolution") for row in belief["evidence"]["covered"]
    ]
    assert "single_family_cycle_advance_reseed_before_monitor_exit" in covered_resolutions
    assert belief["evidence"]["repairable"][0]["position_id"] == "sh-pos"


def test_preflight_passes_when_sidecars_and_live_surfaces_are_fresh(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=now)
    _write_fresh_sidecar_heartbeats(state_dir, now=now)

    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (now.isoformat(), now.isoformat()),
    )
    forecasts.commit()
    trade.close()
    forecasts.close()

    result = preflight.evaluate()

    assert result["ok"] is True
    assert {check["name"] for check in result["checks"] if check["ok"] is False} == set()


def test_preflight_treats_settled_active_position_as_harvester_recovery(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    _add_identity_columns(trade)
    _init_sidecar_surfaces_for_identity(
        trade,
        now=fresh,
        condition_id="cond-la",
        yes_token_id="tok-la-yes",
        no_token_id="tok-la-no",
    )
    label = "Will the highest temperature in Los Angeles be between 72-73°F on June 11?"
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric, bin_label,
            direction, shares, chain_shares, order_status, exit_reason,
            exit_retry_count, next_exit_retry_at, last_monitor_prob,
            last_monitor_prob_is_fresh, last_monitor_market_price,
            last_monitor_market_price_is_fresh, updated_at,
            condition_id, token_id, no_token_id
        ) VALUES (
            'la-pos', 'active', 'Los Angeles', '2026-06-11', 'high',
            ?, 'buy_no', 8.5, 8.5, 'filled', NULL, 0, NULL,
            0.84, 1, 0.72, 1, '2026-06-18T22:39:14+00:00',
            'cond-la', 'tok-la-yes', 'tok-la-no'
        )
        """,
        (label,),
    )
    _insert_monitor_events(trade, position_id="la-pos", monitor_at=fresh, chain_at=fresh)
    stale = datetime.now(timezone.utc) - timedelta(hours=190)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Los Angeles', '2026-06-11', 'high', ?, ?, ?, 'live')
        """,
        (stale.isoformat(), stale.isoformat(), json.dumps({label: 0.15})),
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (2, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (fresh.isoformat(), fresh.isoformat()),
    )
    forecasts.execute(
        """
        INSERT INTO settlement_outcomes (
            city, target_date, market_slug, winning_bin, temperature_metric,
            authority, settlement_source, settlement_value, settled_at
        ) VALUES (
            'Los Angeles', '2026-06-11', 'highest-temperature-in-los-angeles-on-june-11-2026',
            '74-75°F', 'high', 'VERIFIED', 'WU KLAX', 74.0, ?
        )
        """,
        (datetime.now(timezone.utc).isoformat(),),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()

    result = preflight.evaluate()

    assert result["ok"] is True
    belief = next(c for c in result["checks"] if c["name"] == "held_position_belief_coverage")
    assert belief["ok"] is True
    assert belief["evidence"]["risky"] == []
    assert belief["evidence"]["settlement_recoverable"][0]["position_id"] == "la-pos"
    assert (
        belief["evidence"]["settlement_recoverable"][0]["risk"]
        == "verified_settlement_pending_harvester_recovery"
    )


def test_preflight_blocks_settled_active_position_when_harvester_disabled(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    _write_live_plist_with_env(
        preflight.LIVE_TRADING_PLIST_PATH,
        {
            "ZEUS_HARVESTER_LIVE_ENABLED": "0",
            "POLYMARKET_CLOB_V2_SIGNATURE_TYPE": "2",
        },
    )
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    fresh = datetime.now(timezone.utc)
    _write_fresh_sidecar_heartbeats(state_dir, now=fresh)
    _add_identity_columns(trade)
    _init_sidecar_surfaces_for_identity(
        trade,
        now=fresh,
        condition_id="cond-la",
        yes_token_id="tok-la-yes",
        no_token_id="tok-la-no",
    )
    label = "Will the highest temperature in Los Angeles be between 72-73°F on June 11?"
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric, bin_label,
            direction, shares, chain_shares, order_status, exit_reason,
            exit_retry_count, next_exit_retry_at, last_monitor_prob,
            last_monitor_prob_is_fresh, last_monitor_market_price,
            last_monitor_market_price_is_fresh, updated_at,
            condition_id, token_id, no_token_id
        ) VALUES (
            'la-pos', 'active', 'Los Angeles', '2026-06-11', 'high',
            ?, 'buy_no', 8.5, 8.5, 'filled', NULL, 0, NULL,
            0.84, 1, 0.72, 1, '2026-06-18T22:39:14+00:00',
            'cond-la', 'tok-la-yes', 'tok-la-no'
        )
        """,
        (label,),
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Los Angeles', '2026-06-11', 'high', ?, ?, ?, 'live')
        """,
        (fresh.isoformat(), fresh.isoformat(), json.dumps({label: 0.15})),
    )
    forecasts.execute(
        """
        INSERT INTO settlement_outcomes (
            city, target_date, market_slug, winning_bin, temperature_metric,
            authority, settlement_source, settlement_value, settled_at
        ) VALUES (
            'Los Angeles', '2026-06-11', 'highest-temperature-in-los-angeles-on-june-11-2026',
            '74-75°F', 'high', 'VERIFIED', 'WU KLAX', 74.0, ?
        )
        """,
        (fresh.isoformat(),),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()

    result = preflight.evaluate()

    assert result["ok"] is False
    belief = next(c for c in result["checks"] if c["name"] == "held_position_belief_coverage")
    assert belief["ok"] is False
    assert belief["evidence"]["risky"][0]["risk"] == "settled_position_harvester_disabled"


def test_preflight_blocks_open_position_when_only_irrelevant_sidecar_rows_are_fresh(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    target_date = (now + timedelta(days=3)).date().isoformat()
    _write_fresh_sidecar_heartbeats(state_dir, now=now)
    _add_identity_columns(trade)
    label = f"Will the highest temperature in Seattle be between 82-83F on {target_date}?"
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric, bin_label,
            direction, shares, chain_shares, order_status, exit_reason,
            exit_retry_count, next_exit_retry_at, last_monitor_prob,
            last_monitor_prob_is_fresh, last_monitor_market_price,
            last_monitor_market_price_is_fresh, updated_at,
            condition_id, token_id, no_token_id
        ) VALUES (
            'active-pos', 'active', 'Seattle', ?, 'high',
            ?, 'buy_no', 9.0, 9.0, 'filled', NULL, 0, NULL,
            0.84, 1, 0.72, 1, ?, 'cond-target', 'tok-yes-target', 'tok-no-target'
        )
        """,
        (target_date, label, now.isoformat()),
    )
    trade.execute(
        """
        CREATE TABLE execution_feasibility_evidence (
            condition_id TEXT,
            token_id TEXT,
            quote_seen_at TEXT NOT NULL
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE executable_market_snapshots (
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            selected_outcome_token_id TEXT,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL
        )
        """
    )
    trade.execute(
        "INSERT INTO execution_feasibility_evidence VALUES ('cond-other', 'tok-other', ?)",
        (now.isoformat(),),
    )
    trade.execute(
        """
        INSERT INTO executable_market_snapshots VALUES (
            'cond-other', 'tok-other-yes', 'tok-other-no', 'tok-other-yes', ?, ?
        )
        """,
        (now.isoformat(), (now + timedelta(minutes=2)).isoformat()),
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', ?, 'high', ?, ?, ?, 'live')
        """,
        (
            target_date,
            now.isoformat(),
            now.isoformat(),
            json.dumps({label: 0.15}),
        ),
    )
    trade.commit()
    forecasts.commit()
    trade.close()
    forecasts.close()

    result = preflight.evaluate()

    assert result["ok"] is False
    substrate = next(c for c in result["checks"] if c["name"] == "executable_substrate_freshness")
    feasibility = next(c for c in result["checks"] if c["name"] == "execution_feasibility_evidence_freshness")
    assert substrate["ok"] is False
    assert feasibility["ok"] is False
    assert substrate["evidence"]["risky"][0]["risk"] == "missing_executable_substrate"
    assert feasibility["evidence"]["risky"][0]["risk"] == "missing_execution_feasibility_evidence"
    assert substrate["evidence"]["risky"][0]["condition_id"] == "cond-target"
    assert feasibility["evidence"]["risky"][0]["tokens"] == ["tok-no-target", "tok-yes-target"]


def test_execution_feasibility_freshness_uses_observation_time_not_venue_book_timestamp():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    stale_book_time = now - timedelta(minutes=5)
    conn.execute(
        """
        CREATE TABLE execution_feasibility_evidence (
            condition_id TEXT,
            token_id TEXT,
            quote_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO execution_feasibility_evidence VALUES (?, ?, ?, ?)",
        ("cond-target", "tok-no-target", stale_book_time.isoformat(), now.isoformat()),
    )

    result = preflight._execution_feasibility_exposure_freshness(
        conn,
        columns={"condition_id", "token_id", "quote_seen_at", "created_at"},
        exposures=[
            {
                "position_id": "active-pos",
                "phase": "active",
                "city": "London",
                "target_date": "2026-06-19",
                "temperature_metric": "low",
                "bin_label": "Will the lowest temperature in London be 17°C on June 19?",
                "direction": "buy_no",
                "condition_id": "cond-target",
                "tokens": ["tok-no-target"],
            }
        ],
        now=now,
    )

    assert result["risky"] == []
    covered = result["covered"][0]
    assert covered["freshness_basis"] == "created_at"
    assert covered["latest_observed_at"] == now.isoformat()
    assert covered["latest_quote_seen_at"] == stale_book_time.isoformat()


def test_execution_feasibility_freshness_tolerates_small_writer_clock_skew():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    observed_after_check_started = now + timedelta(seconds=1)
    conn.execute(
        """
        CREATE TABLE execution_feasibility_evidence (
            condition_id TEXT,
            token_id TEXT,
            quote_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO execution_feasibility_evidence VALUES (?, ?, ?, ?)",
        (
            "cond-target",
            "tok-no-target",
            now.isoformat(),
            observed_after_check_started.isoformat(),
        ),
    )

    result = preflight._execution_feasibility_exposure_freshness(
        conn,
        columns={"condition_id", "token_id", "quote_seen_at", "created_at"},
        exposures=[
            {
                "position_id": "active-pos",
                "phase": "active",
                "city": "Tokyo",
                "target_date": "2026-06-21",
                "temperature_metric": "low",
                "bin_label": "Will the lowest temperature in Tokyo be 22°C on June 21?",
                "direction": "buy_no",
                "condition_id": "cond-target",
                "tokens": ["tok-no-target"],
            }
        ],
        now=now,
    )

    assert result["risky"] == []
    covered = result["covered"][0]
    assert covered["age_seconds"] == -1.0
    assert covered["clock_skew_tolerated_seconds"] == 1.0


def test_execution_feasibility_freshness_uses_fresh_executable_snapshot_quote():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    stale = now - timedelta(hours=3)
    conn.execute(
        """
        CREATE TABLE execution_feasibility_evidence (
            condition_id TEXT,
            token_id TEXT,
            quote_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            selected_outcome_token_id TEXT,
            outcome_label TEXT,
            active INTEGER,
            closed INTEGER,
            accepting_orders INTEGER,
            orderbook_top_bid TEXT,
            orderbook_top_ask TEXT,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO execution_feasibility_evidence VALUES (?, ?, ?, ?)",
        ("cond-target", "tok-no-target", stale.isoformat(), stale.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshots VALUES (
            'snap-fresh', 'cond-target', 'tok-yes-target', 'tok-no-target',
            'tok-no-target', 'NO', 1, 0, 1, '0.71', '0.73', ?, ?
        )
        """,
        (now.isoformat(), (now + timedelta(minutes=2)).isoformat()),
    )

    result = preflight._execution_feasibility_exposure_freshness(
        conn,
        columns={"condition_id", "token_id", "quote_seen_at", "created_at"},
        exposures=[
            {
                "position_id": "active-pos",
                "phase": "quarantined",
                "city": "Munich",
                "target_date": "2026-06-30",
                "temperature_metric": "high",
                "bin_label": "Will the highest temperature in Munich be 30°C on June 30?",
                "direction": "buy_no",
                "condition_id": "cond-target",
                "tokens": ["tok-no-target", "tok-yes-target"],
            }
        ],
        now=now,
    )

    assert result["risky"] == []
    covered = result["covered"][0]
    assert covered["freshness_basis"] == "executable_market_snapshots.captured_at"
    assert covered["snapshot_id"] == "snap-fresh"
    assert covered["execution_feasibility_age_seconds"] > 3600


def test_execution_feasibility_freshness_accepts_negrisk_child_snapshot_active_false():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    stale = now - timedelta(hours=3)
    conn.execute(
        """
        CREATE TABLE execution_feasibility_evidence (
            condition_id TEXT,
            token_id TEXT,
            quote_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            selected_outcome_token_id TEXT,
            outcome_label TEXT,
            enable_orderbook INTEGER,
            active INTEGER,
            closed INTEGER,
            accepting_orders INTEGER,
            orderbook_top_bid TEXT,
            orderbook_top_ask TEXT,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO execution_feasibility_evidence VALUES (?, ?, ?, ?)",
        ("cond-target", "tok-yes-target", stale.isoformat(), stale.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshots VALUES (
            'snap-active-false', 'cond-target', 'tok-yes-target', 'tok-no-target',
            'tok-yes-target', 'YES', 1, 0, 0, 1, '0.03', '0.04', ?, ?
        )
        """,
        (now.isoformat(), (now + timedelta(minutes=2)).isoformat()),
    )

    result = preflight._execution_feasibility_exposure_freshness(
        conn,
        columns={"condition_id", "token_id", "quote_seen_at", "created_at"},
        exposures=[
            {
                "position_id": "active-pos",
                "phase": "active",
                "city": "Buenos Aires",
                "target_date": "2026-07-02",
                "temperature_metric": "high",
                "bin_label": "Will the highest temperature in Buenos Aires be 11°C on July 2?",
                "direction": "buy_yes",
                "condition_id": "cond-target",
                "tokens": ["tok-yes-target"],
            }
        ],
        now=now,
    )

    assert result["risky"] == []
    covered = result["covered"][0]
    assert covered["freshness_basis"] == "executable_market_snapshots.captured_at"
    assert covered["snapshot_id"] == "snap-active-false"
    assert covered["outcome_label"] == "YES"


def test_execution_feasibility_freshness_blocks_large_future_timestamp():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    future = now + timedelta(
        seconds=preflight.EXECUTION_FEASIBILITY_CLOCK_SKEW_TOLERANCE_SECONDS + 1
    )
    conn.execute(
        """
        CREATE TABLE execution_feasibility_evidence (
            condition_id TEXT,
            token_id TEXT,
            quote_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO execution_feasibility_evidence VALUES (?, ?, ?, ?)",
        ("cond-target", "tok-no-target", now.isoformat(), future.isoformat()),
    )

    result = preflight._execution_feasibility_exposure_freshness(
        conn,
        columns={"condition_id", "token_id", "quote_seen_at", "created_at"},
        exposures=[
            {
                "position_id": "active-pos",
                "phase": "active",
                "city": "Tokyo",
                "target_date": "2026-06-21",
                "temperature_metric": "low",
                "bin_label": "Will the lowest temperature in Tokyo be 22°C on June 21?",
                "direction": "buy_no",
                "condition_id": "cond-target",
                "tokens": ["tok-no-target"],
            }
        ],
        now=now,
    )

    assert result["risky"][0]["risk"] == "future_execution_feasibility_evidence"


def test_executable_quote_not_required_after_venue_close(monkeypatch):
    from src.strategy import market_phase

    monkeypatch.setattr(market_phase, "family_venue_closed", lambda **_: True)
    now = datetime.now(timezone.utc)

    for phase in ("active", "day0_window", "pending_exit"):
        assert preflight._requires_executable_quote(
            {
                "phase": phase,
                "city": "Singapore",
                "target_date": "2026-06-19",
            },
            now_utc=now,
        ) is False


def test_executable_quote_required_before_venue_close(monkeypatch):
    from src.strategy import market_phase

    monkeypatch.setattr(market_phase, "family_venue_closed", lambda **_: False)
    now = datetime.now(timezone.utc)

    assert preflight._requires_executable_quote(
        {
            "phase": "day0_window",
            "city": "Paris",
            "target_date": "2026-06-20",
        },
        now_utc=now,
    ) is True


def test_preflight_blocks_missing_sidecar_heartbeat(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=now)
    _write_fresh_sidecar_heartbeats(state_dir, now=now)
    (state_dir / "daemon-heartbeat-price-channel-ingest.json").unlink()
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (now.isoformat(), now.isoformat()),
    )
    forecasts.commit()
    trade.close()
    forecasts.close()

    result = preflight.evaluate()

    assert result["ok"] is False
    heartbeat = next(c for c in result["checks"] if c["name"] == "price_channel_daemon_heartbeat")
    assert heartbeat["ok"] is False
    assert heartbeat["detail"] == "sidecar heartbeat file is missing"


def test_preflight_blocks_sidecar_heartbeat_without_code_identity(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=now)
    _write_fresh_sidecar_heartbeats(state_dir, now=now)
    (state_dir / "daemon-heartbeat-substrate-observer.json").write_text(
        json.dumps({"alive_at": now.isoformat(), "pid": 123})
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (now.isoformat(), now.isoformat()),
    )
    forecasts.commit()
    trade.close()
    forecasts.close()

    result = preflight.evaluate()

    assert result["ok"] is False
    heartbeat = next(c for c in result["checks"] if c["name"] == "substrate_observer_daemon_heartbeat")
    assert heartbeat["ok"] is False
    assert heartbeat["detail"] == "sidecar heartbeat git head is missing"


def test_preflight_blocks_sidecar_heartbeat_on_stale_code_identity(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=now)
    _write_fresh_sidecar_heartbeats(state_dir, now=now)
    (state_dir / "daemon-heartbeat-price-channel-ingest.json").write_text(
        json.dumps({"alive_at": now.isoformat(), "pid": 123, "git_head": "oldsha"})
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (now.isoformat(), now.isoformat()),
    )
    forecasts.commit()
    trade.close()
    forecasts.close()

    result = preflight.evaluate()

    assert result["ok"] is False
    heartbeat = next(c for c in result["checks"] if c["name"] == "price_channel_daemon_heartbeat")
    assert heartbeat["ok"] is False
    assert heartbeat["detail"] == "sidecar heartbeat git head does not match current code"


def test_monitor_cadence_restart_evidence_records_recovery_obligation_when_main_absent(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    conn = _init_trade_db(trade_db)
    _insert_open_position_with_monitor_events(
        conn,
        monitor_at=datetime.now(timezone.utc) - timedelta(minutes=20),
    )
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "_live_main_processes", lambda: [])

    result = preflight._monitor_cadence_restart_evidence_check(preflight._open_positions())

    assert result.ok is True
    assert result.evidence["restart_recovery_obligation"].startswith("post-start health")
    assert result.evidence["position_current_updated_at_is_not_monitor_cadence"] is True


def test_monitor_cadence_restart_evidence_blocks_running_stale_main(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    conn = _init_trade_db(trade_db)
    _insert_open_position_with_monitor_events(
        conn,
        monitor_at=datetime.now(timezone.utc) - timedelta(minutes=20),
    )
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "_live_main_processes", lambda: ["123 python -m src.main"])

    result = preflight._monitor_cadence_restart_evidence_check(preflight._open_positions())

    assert result.ok is False
    assert result.detail == "src.main is running but held-position monitor cadence is stale"
    assert result.evidence["live_main_processes"] == ["123 python -m src.main"]


def test_monitor_cadence_restart_evidence_accepts_closed_market_settlement_recovery(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    conn = _init_trade_db(trade_db)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            bin_label, direction, shares, chain_shares, order_status,
            exit_reason, exit_retry_count, next_exit_retry_at,
            last_monitor_prob, last_monitor_prob_is_fresh,
            last_monitor_market_price, last_monitor_market_price_is_fresh,
            updated_at
        ) VALUES (
            'pos-1', 'day0_window', 'Wellington', '2026-07-02', 'high',
            'Will the highest temperature in Wellington be 12°C on July 2?',
            'buy_yes', 15.0, 15.0, 'filled', NULL, 0, NULL,
            0.0, 1, NULL, 0, ?
        )
        """,
        (now.isoformat(),),
    )
    _insert_monitor_events(
        conn,
        monitor_at=now - timedelta(minutes=20),
        payload={"applied_validations": ["day0_hard_fact_bin_dead_closed_market"]},
    )
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "_live_main_processes", lambda: ["123 python -m src.main"])

    result = preflight._monitor_cadence_restart_evidence_check(preflight._open_positions())

    assert result.ok is True
    assert result.detail == "all held-position monitor cadence evidence is fresh or settlement-recoverable"
    assert result.evidence["stale_or_missing_position_count"] == 0
    assert result.evidence["settlement_recoverable_position_count"] == 1
    assert result.evidence["settlement_recoverable_positions"][0]["position_id"] == "pos-1"


def test_monitor_cadence_restart_evidence_blocks_unmonitored_entry_authority_quarantine(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    conn = _init_trade_db(trade_db)
    conn.execute("ALTER TABLE position_current ADD COLUMN chain_state TEXT")
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            bin_label, direction, shares, chain_shares, order_status,
            exit_reason, exit_retry_count, next_exit_retry_at,
            last_monitor_prob, last_monitor_prob_is_fresh,
            last_monitor_market_price, last_monitor_market_price_is_fresh,
            updated_at, chain_state
        ) VALUES (
            'pos-1', 'quarantined', 'Manila', '2026-07-02', 'high',
            'Will the highest temperature in Manila be 32°C on July 2?',
            'buy_yes', 10.0, 10.0, 'filled', NULL, 0, NULL,
            0.0, 1, NULL, 0, ?, 'entry_authority_quarantined'
        )
        """,
        (now.isoformat(),),
    )
    _insert_monitor_events(conn, monitor_at=now - timedelta(minutes=20))
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "_live_main_processes", lambda: ["123 python -m src.main"])

    result = preflight._monitor_cadence_restart_evidence_check(preflight._open_positions())

    assert result.ok is False
    assert result.detail == "src.main is running but held-position monitor cadence is stale"
    assert result.evidence["stale_or_missing_position_count"] == 1
    assert result.evidence["stale_or_missing_positions"][0]["position_id"] == "pos-1"
    assert result.evidence["non_monitor_chain_risk_position_count"] == 0


def test_monitor_cadence_restart_evidence_accepts_pending_exit_redecision_event(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    conn = _init_trade_db(trade_db)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            bin_label, direction, shares, chain_shares, order_status,
            exit_reason, exit_retry_count, next_exit_retry_at,
            last_monitor_prob, last_monitor_prob_is_fresh,
            last_monitor_market_price, last_monitor_market_price_is_fresh,
            updated_at
        ) VALUES (
            'pos-1', 'pending_exit', 'Manila', '2026-07-02', 'high',
            'Will the highest temperature in Manila be 32°C on July 2?',
            'buy_yes', 10.0, 10.0, 'retry_pending',
            'DAY0_HARD_FACT_BIN_DEAD', 1, ?,
            0.0, 1, 0.0, 1, ?
        )
        """,
        ((now + timedelta(minutes=5)).isoformat(), now.isoformat()),
    )
    _insert_monitor_events(
        conn,
        monitor_at=now - timedelta(minutes=20),
        chain_at=now,
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at, payload_json
        ) VALUES (
            'evt-exit-rejected', 'pos-1', 3, 'EXIT_ORDER_REJECTED', ?, '{}'
        )
        """,
        ((now - timedelta(seconds=20)).isoformat(),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "_live_main_processes", lambda: ["123 python -m src.main"])

    result = preflight._monitor_cadence_restart_evidence_check(preflight._open_positions())

    assert result.ok is True
    assert result.evidence["fresh_position_count"] == 1
    assert result.evidence["stale_or_missing_position_count"] == 0


def test_monitor_cadence_restart_evidence_is_per_position_not_global_latest(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    conn = _init_trade_db(trade_db)
    _insert_open_position_with_monitor_events(
        conn,
        monitor_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            bin_label, direction, shares, chain_shares, order_status,
            exit_reason, exit_retry_count, next_exit_retry_at,
            last_monitor_prob, last_monitor_prob_is_fresh,
            last_monitor_market_price, last_monitor_market_price_is_fresh,
            updated_at
        ) VALUES (
            'pos-2', 'active', 'Paris', '2026-07-02', 'low',
            'Will the lowest temperature in Paris be 19°C on July 2?',
            'buy_no', 5.0, 5.0, 'filled', NULL, 0, NULL,
            0.81, 1, 0.75, 1, ?
        )
        """,
        (now.isoformat(),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "_live_main_processes", lambda: ["123 python -m src.main"])

    result = preflight._monitor_cadence_restart_evidence_check(preflight._open_positions())

    assert result.ok is False
    assert result.evidence["fresh_position_count"] == 1
    assert result.evidence["stale_or_missing_position_count"] == 1
    assert result.evidence["stale_or_missing_positions"][0]["position_id"] == "pos-2"


def test_monitor_cadence_restart_evidence_rejects_future_events_even_main_absent(
    monkeypatch, tmp_path
):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    sqlite3.connect(world_db).close()
    sqlite3.connect(forecast_db).close()
    conn = _init_trade_db(trade_db)
    _insert_open_position_with_monitor_events(
        conn,
        monitor_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    conn.close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "_live_main_processes", lambda: [])

    result = preflight._monitor_cadence_restart_evidence_check(preflight._open_positions())

    assert result.ok is False
    assert result.detail == "held-position monitor cadence has future-dated events"
    assert result.evidence["future_monitor_event_count"] == 1


# --- B1: submit_authority_config fail-closed tests ---


def _write_settings_with_edli(settings_path, *, real_order_submit_enabled, reactor_mode, live_execution_mode):
    settings_path.write_text(
        json.dumps(
            {
                "edli": {
                    "real_order_submit_enabled": real_order_submit_enabled,
                    "reactor_mode": reactor_mode,
                    "live_execution_mode": live_execution_mode,
                },
                "feature_flags": {"qkernel_spine_enabled": True},
            }
        )
    )


def test_preflight_blocks_armed_live_when_real_submit_disabled(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=now)
    _write_fresh_sidecar_heartbeats(state_dir, now=now)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (now.isoformat(), now.isoformat()),
    )
    forecasts.commit()
    trade.close()
    forecasts.close()
    _write_settings_with_edli(
        preflight.SETTINGS_PATH,
        real_order_submit_enabled=False,
        reactor_mode="live",
        live_execution_mode="edli_live",
    )

    result = preflight.evaluate()

    assert result["ok"] is False
    submit = next(c for c in result["checks"] if c["name"] == "submit_authority_config")
    assert submit["ok"] is False
    assert any(c["name"] == "submit_authority_config" for c in result["blockers"])
    # main() should return nonzero
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = preflight.main(["--json"])
    assert exit_code != 0


def test_preflight_blocks_armed_live_when_reactor_mode_live_no_submit(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=now)
    _write_fresh_sidecar_heartbeats(state_dir, now=now)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (now.isoformat(), now.isoformat()),
    )
    forecasts.commit()
    trade.close()
    forecasts.close()
    _write_settings_with_edli(
        preflight.SETTINGS_PATH,
        real_order_submit_enabled=True,
        reactor_mode="live_no_submit",
        live_execution_mode="edli_live",
    )

    result = preflight.evaluate()

    assert result["ok"] is False
    submit = next(c for c in result["checks"] if c["name"] == "submit_authority_config")
    assert submit["ok"] is False
    assert any(c["name"] == "submit_authority_config" for c in result["blockers"])


def test_preflight_blocks_missing_live_execution_mode_instead_of_legacy_default(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=now)
    _write_fresh_sidecar_heartbeats(state_dir, now=now)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (now.isoformat(), now.isoformat()),
    )
    forecasts.commit()
    trade.close()
    forecasts.close()
    preflight.SETTINGS_PATH.write_text(
        json.dumps(
            {
                "edli": {
                    "real_order_submit_enabled": True,
                    "reactor_mode": "live",
                },
                "feature_flags": {"qkernel_spine_enabled": True},
            }
        )
    )

    result = preflight.evaluate()

    submit = next(c for c in result["checks"] if c["name"] == "submit_authority_config")
    assert submit["ok"] is False
    assert submit["evidence"]["edli.live_execution_mode"] == "missing"
    assert submit["evidence"]["known_execution_mode"] is False
    assert any(c["name"] == "submit_authority_config" for c in result["blockers"])


def test_preflight_submit_authority_passes_when_reactor_mode_live_and_real_submit_enabled(
    monkeypatch, tmp_path
):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    _init_sidecar_surfaces(trade, now=now)
    _write_fresh_sidecar_heartbeats(state_dir, now=now)
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, q_json, runtime_layer
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, '{}', 'live')
        """,
        (now.isoformat(), now.isoformat()),
    )
    forecasts.commit()
    trade.close()
    forecasts.close()
    _write_settings_with_edli(
        preflight.SETTINGS_PATH,
        real_order_submit_enabled=True,
        reactor_mode="live",
        live_execution_mode="edli_live",
    )

    result = preflight.evaluate()

    submit = next(c for c in result["checks"] if c["name"] == "submit_authority_config")
    assert submit["ok"] is True
    assert not any(c["name"] == "submit_authority_config" for c in result["blockers"])
