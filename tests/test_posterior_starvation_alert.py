# Created: 2026-07-17
# Last reused or audited: 2026-07-17
# Authority basis: task instruction "P1 observability fix" (2026-07-17), incident
#   2026-07-13/14 CONUS live-posterior blackout (30-37h dark, no operator signal).
"""Posterior-starvation alert antibody.

Background: 2026-07-13/14 all CONUS cities' live posteriors went dark for
30-37h (materialization BLOCKED every ~5min; entries silently starved once the
30h ``expires_at`` TTL passed). No existing watchdog covered "a family with a
live market has no fresh live posterior" — heartbeat_supervisor covers process
heartbeat, riskguard covers position reference, the monitor-cadence watchdog
(src.execution.exit_lifecycle) covers monitor cadence. This test locks the new
``_posterior_starvation_surface`` (src/control/live_health.py), wired as the
20th surface of ``compute_composite_live_health``, so a future revert of the
alert (or its silent exclusion from logging) goes RED.

Invariant: log-only alert, not a gate. This surface name is deliberately
absent from ``src.engine.event_reactor_adapter._ENTRY_LIVE_HEALTH_REQUIRED_SURFACES``
(checked directly in T6) so a starved family can never itself block a live
entry — the existing freshness gates already fail closed on the money path.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.control.live_health import (
    POSTERIOR_STALENESS_ALERT_HOURS_DEFAULT,
    _posterior_staleness_alert_hours,
    _posterior_starvation_surface,
    compute_composite_live_health,
)


def _now_iso(now: datetime, offset_hours: float = 0.0) -> str:
    return (now + timedelta(hours=offset_hours)).isoformat()


def _write_market_events(
    sd: Path,
    *,
    city: str,
    target_date: str,
    metric: str,
    token_id: str | None,
    created_at: str,
) -> None:
    conn = sqlite3.connect(sd / "zeus-forecasts.db")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS market_events ("
            "city TEXT, target_date TEXT, temperature_metric TEXT, "
            "token_id TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO market_events "
            "(city, target_date, temperature_metric, token_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (city, target_date, metric, token_id, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def _write_forecast_posterior(
    sd: Path,
    *,
    city: str,
    target_date: str,
    metric: str,
    runtime_layer: str,
    computed_at: str,
) -> None:
    conn = sqlite3.connect(sd / "zeus-forecasts.db")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS forecast_posteriors ("
            "city TEXT, target_date TEXT, temperature_metric TEXT, "
            "runtime_layer TEXT, computed_at TEXT)"
        )
        conn.execute(
            "INSERT INTO forecast_posteriors "
            "(city, target_date, temperature_metric, runtime_layer, computed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (city, target_date, metric, runtime_layer, computed_at),
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_forecast_posteriors_table(sd: Path) -> None:
    conn = sqlite3.connect(sd / "zeus-forecasts.db")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS forecast_posteriors ("
            "city TEXT, target_date TEXT, temperature_metric TEXT, "
            "runtime_layer TEXT, computed_at TEXT)"
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# T1: stale live posterior on a live-tradeable market -> ERROR log + surface fail
# ---------------------------------------------------------------------------

def test_stale_posterior_emits_error_and_fails_surface(tmp_path, caplog):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    target_date = "2026-07-17"

    _write_market_events(
        sd,
        city="Chicago",
        target_date=target_date,
        metric="high",
        token_id="tok-chicago-high",
        created_at=_now_iso(now, -48.0),
    )
    _write_forecast_posterior(
        sd,
        city="Chicago",
        target_date=target_date,
        metric="high",
        runtime_layer="live",
        computed_at=_now_iso(now, -20.0),
    )

    with caplog.at_level(logging.ERROR, logger="src.control.live_health"):
        result = _posterior_starvation_surface(sd, now)

    assert result["ok"] is False
    assert result["issue"] == "POSTERIOR_STARVATION:n=1"
    assert result["starved_count"] == 1
    starved = result["starved_sample"][0]
    assert starved["city"] == "Chicago"
    assert starved["target_date"] == target_date
    assert starved["metric"] == "high"
    assert starved["has_posterior"] is True
    assert 19.9 < starved["age_h"] < 20.1

    [record] = [r for r in caplog.records if "ZEUS_POSTERIOR_STARVATION" in r.message]
    assert record.levelno == logging.ERROR
    assert "city=Chicago" in record.message
    assert f"target={target_date}" in record.message
    assert "metric=high" in record.message
    assert "age_h=20.0" in record.message
    assert "newest_blocked_reason=unknown" in record.message


# ---------------------------------------------------------------------------
# T2: fresh live posterior -> silent
# ---------------------------------------------------------------------------

def test_fresh_posterior_is_silent(tmp_path, caplog):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    target_date = "2026-07-17"

    _write_market_events(
        sd,
        city="Denver",
        target_date=target_date,
        metric="low",
        token_id="tok-denver-low",
        created_at=_now_iso(now, -48.0),
    )
    _write_forecast_posterior(
        sd,
        city="Denver",
        target_date=target_date,
        metric="low",
        runtime_layer="live",
        computed_at=_now_iso(now, -1.0),
    )

    with caplog.at_level(logging.ERROR, logger="src.control.live_health"):
        result = _posterior_starvation_surface(sd, now)

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["starved_count"] == 0
    assert not [r for r in caplog.records if "ZEUS_POSTERIOR_STARVATION" in r.message]


# ---------------------------------------------------------------------------
# T3: no live-tradeable market at all -> silent
# ---------------------------------------------------------------------------

def test_no_market_is_silent(tmp_path, caplog):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    # market_events table exists but has zero rows (no live market anywhere).
    conn = sqlite3.connect(sd / "zeus-forecasts.db")
    conn.execute(
        "CREATE TABLE market_events (city TEXT, target_date TEXT, "
        "temperature_metric TEXT, token_id TEXT, created_at TEXT)"
    )
    conn.commit()
    conn.close()
    _ensure_forecast_posteriors_table(sd)

    with caplog.at_level(logging.ERROR, logger="src.control.live_health"):
        result = _posterior_starvation_surface(sd, now)

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["starved_count"] == 0
    assert not [r for r in caplog.records if "ZEUS_POSTERIOR_STARVATION" in r.message]


def test_missing_market_events_table_skips_gracefully(tmp_path):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)

    result = _posterior_starvation_surface(sd, now)

    assert result["ok"] is True
    assert result["evaluated"] is False


# ---------------------------------------------------------------------------
# T4: no live posterior EVER for a family known > threshold -> alert
# ---------------------------------------------------------------------------

def test_missing_posterior_entirely_alerts_on_family_age(tmp_path, caplog):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    target_date = "2026-07-17"

    _write_market_events(
        sd,
        city="Shanghai",
        target_date=target_date,
        metric="low",
        token_id="tok-shanghai-low",
        created_at=_now_iso(now, -15.0),
    )
    _ensure_forecast_posteriors_table(sd)

    with caplog.at_level(logging.ERROR, logger="src.control.live_health"):
        result = _posterior_starvation_surface(sd, now)

    assert result["ok"] is False
    starved = result["starved_sample"][0]
    assert starved["has_posterior"] is False
    assert starved["city"] == "Shanghai"
    assert [r for r in caplog.records if "ZEUS_POSTERIOR_STARVATION" in r.message]


def test_empty_token_id_is_not_a_live_market(tmp_path):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    target_date = "2026-07-17"

    _write_market_events(
        sd,
        city="Miami",
        target_date=target_date,
        metric="high",
        token_id="",
        created_at=_now_iso(now, -48.0),
    )
    _ensure_forecast_posteriors_table(sd)

    result = _posterior_starvation_surface(sd, now)

    assert result["ok"] is True
    assert result["starved_count"] == 0


def test_past_target_date_market_is_excluded(tmp_path):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)

    _write_market_events(
        sd,
        city="Miami",
        target_date="2026-07-10",
        metric="high",
        token_id="tok-miami-high",
        created_at=_now_iso(now, -200.0),
    )
    _ensure_forecast_posteriors_table(sd)

    result = _posterior_starvation_surface(sd, now)

    assert result["ok"] is True
    assert result["starved_count"] == 0


# ---------------------------------------------------------------------------
# T5: newest_blocked_reason enrichment from the failed-materialization sidecar
# ---------------------------------------------------------------------------

def test_newest_blocked_reason_reads_newest_failed_receipt(tmp_path):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    target_date = "2026-07-17"

    _write_market_events(
        sd,
        city="Shanghai",
        target_date=target_date,
        metric="low",
        token_id="tok-shanghai-low",
        created_at=_now_iso(now, -48.0),
    )
    _write_forecast_posterior(
        sd,
        city="Shanghai",
        target_date=target_date,
        metric="low",
        runtime_layer="live",
        computed_at=_now_iso(now, -20.0),
    )
    failed_dir = sd / "replacement_forecast_live" / "failed"
    failed_dir.mkdir(parents=True)
    (failed_dir / f"Shanghai.{target_date}.low.20260713T000000Z.20260713T000100Z.json.receipt.json").write_text(
        json.dumps({"returncode": 2, "stderr": "older failure, should be superseded"})
    )
    (failed_dir / f"Shanghai.{target_date}.low.20260716T230000Z.20260716T230100Z.json.receipt.json").write_text(
        json.dumps({"returncode": 2, "stderr": "MATERIALIZATION_FAILED: newest reason"})
    )

    result = _posterior_starvation_surface(sd, now)

    starved = result["starved_sample"][0]
    assert starved["newest_blocked_reason"] == "MATERIALIZATION_FAILED: newest reason"


# ---------------------------------------------------------------------------
# T6: composite wiring + not an entry gate
# ---------------------------------------------------------------------------

def test_composite_includes_posterior_starvation_surface(tmp_path):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    target_date = "2026-07-17"

    _write_market_events(
        sd,
        city="Chicago",
        target_date=target_date,
        metric="high",
        token_id="tok-chicago-high",
        created_at=_now_iso(now, -48.0),
    )
    _write_forecast_posterior(
        sd,
        city="Chicago",
        target_date=target_date,
        metric="high",
        runtime_layer="live",
        computed_at=_now_iso(now, -20.0),
    )

    result = compute_composite_live_health(state_dir=sd, now=now)

    assert "posterior_starvation" in result["surfaces"]
    assert result["surfaces"]["posterior_starvation"]["ok"] is False
    assert "posterior_starvation" in result["failing_surfaces"]


def test_posterior_starvation_is_not_an_entry_gate_surface():
    from src.engine.event_reactor_adapter import _ENTRY_LIVE_HEALTH_REQUIRED_SURFACES

    assert "posterior_starvation" not in _ENTRY_LIVE_HEALTH_REQUIRED_SURFACES


# ---------------------------------------------------------------------------
# Config threshold
# ---------------------------------------------------------------------------

def test_default_threshold_is_twelve_hours():
    assert POSTERIOR_STALENESS_ALERT_HOURS_DEFAULT == 12.0
    assert _posterior_staleness_alert_hours() == 12.0


def test_threshold_reads_ops_config(monkeypatch):
    from src.config import settings

    original = dict(settings._data)
    settings._data["ops"] = {"posterior_staleness_alert_hours": 6.0}
    try:
        assert _posterior_staleness_alert_hours() == 6.0
    finally:
        settings._data.clear()
        settings._data.update(original)
