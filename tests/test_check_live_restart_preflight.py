# Lifecycle: created=2026-06-18; last_reviewed=2026-06-18; last_reused=2026-06-18
# Purpose: Regression tests for read-only live restart preflight risk classification.
# Reuse: pytest tests/test_check_live_restart_preflight.py
# Authority basis: AGENTS.md live-money restart proof gates.

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from scripts import check_live_restart_preflight as preflight


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
    now = datetime.now(timezone.utc).isoformat()
    settings.write_text(json.dumps({"edli": {"real_order_submit_enabled": True}}))
    scheduler_health.write_text(
        json.dumps(
            {
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
    sqlite3.connect(world_db).close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "SETTINGS_PATH", settings)
    monkeypatch.setattr(preflight, "STATE_DIR", state_dir)
    monkeypatch.setattr(preflight, "SCHEDULER_HEALTH_PATH", scheduler_health)
    monkeypatch.setattr(preflight, "FORECAST_LIVE_HEARTBEAT_PATH", forecast_live_heartbeat)
    monkeypatch.setattr(preflight, "_live_main_processes", lambda: [])
    monkeypatch.setattr(preflight, "_git_head", lambda: "testsha")
    return trade_db, forecast_db, state_dir


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


def test_preflight_blocks_unhealthy_replacement_forecast_sidecar(monkeypatch, tmp_path):
    trade_db, forecast_db = _patch_paths(monkeypatch, tmp_path)
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


def test_preflight_blocks_forecast_live_heartbeat_missing_replacement_jobs(monkeypatch, tmp_path):
    trade_db, forecast_db = _patch_paths(monkeypatch, tmp_path)
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


def test_preflight_blocks_running_replacement_forecast_sidecar_job(monkeypatch, tmp_path):
    trade_db, forecast_db = _patch_paths(monkeypatch, tmp_path)
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
    health["replacement_forecast_download"] = {
        "status": "RUNNING",
        "last_run_at": fresh.isoformat(),
        "last_started_at": fresh.isoformat(),
    }
    preflight.SCHEDULER_HEALTH_PATH.write_text(json.dumps(health))

    result = preflight.evaluate()

    assert result["ok"] is False
    sidecar = next(c for c in result["checks"] if c["name"] == "forecast_sidecar_health")
    assert sidecar["ok"] is False
    risks = {item["risk"] for item in sidecar["evidence"]["risky"]}
    assert "scheduler_job_not_ok" in risks


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

    result = preflight.evaluate()

    assert result["ok"] is False
    belief = next(c for c in result["checks"] if c["name"] == "held_position_belief_coverage")
    assert belief["ok"] is False
    assert belief["evidence"]["risky"][0]["risk"] == "stale_live_belief"


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


def test_preflight_blocks_open_position_when_only_irrelevant_sidecar_rows_are_fresh(monkeypatch, tmp_path):
    trade_db, forecast_db, state_dir = _patch_paths(monkeypatch, tmp_path)
    trade = _init_trade_db(trade_db)
    forecasts = _init_forecast_db(forecast_db)
    now = datetime.now(timezone.utc)
    _write_fresh_sidecar_heartbeats(state_dir, now=now)
    trade.execute("ALTER TABLE position_current ADD COLUMN condition_id TEXT")
    trade.execute("ALTER TABLE position_current ADD COLUMN token_id TEXT")
    trade.execute("ALTER TABLE position_current ADD COLUMN no_token_id TEXT")
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
