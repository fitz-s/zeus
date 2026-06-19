# Lifecycle: created=2026-06-18; last_reviewed=2026-06-19; last_reused=2026-06-19
# Purpose: Regression tests for read-only live restart preflight risk classification.
# Reuse: pytest tests/test_check_live_restart_preflight.py
# Authority basis: AGENTS.md live-money restart proof gates.

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from scripts import check_live_restart_preflight as preflight
from src.decision import qlcb_reliability_guard as guard_mod


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
                "edli": {"real_order_submit_enabled": True},
                "feature_flags": {"qkernel_spine_enabled": True},
            }
        )
    )
    live_plist.write_bytes(
        (
            b"""<?xml version="1.0" encoding="UTF-8"?>\n"""
            b"""<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" """
            b""""http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n"""
            b"""<plist version="1.0"><dict><key>EnvironmentVariables</key><dict>"""
            b"""<key>ZEUS_HARVESTER_LIVE_ENABLED</key><string>1</string>"""
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
                "cells": {
                    "high|L1|YES|modal|qb1": {"n": 100, "hit_rate": 0.80},
                    "high|L1|NO|nonmodal|qb1": {"n": 100, "hit_rate": 0.80},
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
        guard_mod,
        "_QLCB_OOF_RELIABILITY_PATH",
        str(state_dir / "qlcb_oof_reliability.json"),
    )
    guard_mod.reset_reliability_cache()
    monkeypatch.delenv("ZEUS_HARVESTER_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("ZEUS_LIVE_FAMILY_PORTFOLIO_MAX_LEGS", raising=False)
    monkeypatch.setattr(preflight, "_live_main_processes", lambda: [])
    monkeypatch.setattr(preflight, "_git_head", lambda: "testsha")
    return trade_db, forecast_db, state_dir


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
    conn.commit()


def _write_fresh_sidecar_heartbeats(state_dir, *, now: datetime):
    for _, filename in preflight.SIDECAR_HEARTBEATS:
        (state_dir / filename).write_text(json.dumps({"alive_at": now.isoformat(), "pid": 123}))


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


def test_preflight_accepts_stale_belief_when_single_family_reseed_is_materializable(
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
            'karachi-pos', 'day0_window', 'Karachi', '2026-06-19', 'high',
            ?, 'buy_no', 5.0, 5.0, 'filled', NULL, 0, NULL,
            0.84, 1, 0.72, 1, '2026-06-18T23:00:00+00:00',
            'cond-karachi', 'tok-karachi-yes', 'tok-karachi-no'
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
    repair = belief["evidence"]["repairable"][0]
    assert repair["position_id"] == "karachi-pos"
    assert repair["risk"] == "stale_live_belief_repairable_by_single_family_reseed"
    assert repair["posterior_id"] == "1"


def test_preflight_accepts_missing_belief_when_single_family_reseed_is_materializable(
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
            'sh-pos', 'day0_window', 'Shanghai', '2026-06-19', 'high',
            ?, 'buy_no', 5.0, 5.0, 'filled', NULL, 0, NULL,
            0.84, 0, 0.72, 1, '2026-06-19T01:00:00+00:00',
            'cond-sh', 'tok-sh-yes', 'tok-sh-no'
        )
        """,
        (label,),
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
    monkeypatch.setenv("ZEUS_HARVESTER_LIVE_ENABLED", "0")
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
    _write_fresh_sidecar_heartbeats(state_dir, now=now)
    _add_identity_columns(trade)
    label = "Will the highest temperature in Seattle be between 82-83°F on June 19?"
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
            'active-pos', 'active', 'Seattle', '2026-06-19', 'high',
            ?, 'buy_no', 9.0, 9.0, 'filled', NULL, 0, NULL,
            0.84, 1, 0.72, 1, ?, 'cond-target', 'tok-yes-target', 'tok-no-target'
        )
        """,
        (label, now.isoformat()),
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
        ) VALUES (1, 'Seattle', '2026-06-19', 'high', ?, ?, ?, 'live')
        """,
        (
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
