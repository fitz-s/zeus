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
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"edli": {"real_order_submit_enabled": True}}))
    sqlite3.connect(world_db).close()
    monkeypatch.setattr(preflight, "TRADE_DB", trade_db)
    monkeypatch.setattr(preflight, "WORLD_DB", world_db)
    monkeypatch.setattr(preflight, "FORECAST_DB", forecast_db)
    monkeypatch.setattr(preflight, "SETTINGS_PATH", settings)
    monkeypatch.setattr(preflight, "_live_main_processes", lambda: [])
    monkeypatch.setattr(preflight, "_git_head", lambda: "testsha")
    return trade_db, forecast_db


def test_preflight_blocks_dust_projection_that_would_reload_as_pending_exit(monkeypatch, tmp_path):
    trade_db, forecast_db = _patch_paths(monkeypatch, tmp_path)
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
    trade_db, forecast_db = _patch_paths(monkeypatch, tmp_path)
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
