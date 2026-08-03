# Created: 2026-07-17
# Last reused/audited: 2026-08-03
# Lifecycle: created=2026-07-17; last_reviewed=2026-08-03; last_reused=2026-08-03
# Purpose: Lock the posterior-starvation alert's scope, visibility, and non-gating behavior.
# Reuse: Run when live-health posterior freshness, city timezones, or alert wiring changes.
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
absent from ``src.engine.event_reactor_adapter`` (checked directly in T6) so
a starved family can never itself block a live entry — the existing freshness
gates already fail closed on the money path.
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
    _target_local_day_complete,
    compute_composite_live_health,
)


def _now_iso(now: datetime, offset_hours: float = 0.0) -> str:
    return (now + timedelta(hours=offset_hours)).isoformat()


def test_target_local_day_complete_keeps_scope_visible_on_config_reload_failure(
    monkeypatch,
) -> None:
    import src.config

    def fail_reload():
        raise OSError("transient config reload failure")

    monkeypatch.setattr(src.config, "runtime_cities_by_name", fail_reload)

    assert not _target_local_day_complete(
        "Hong Kong",
        "2026-07-27",
        datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
    )


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


def test_day0_family_requires_remaining_day_authority_not_replacement_posterior(
    tmp_path,
    monkeypatch,
):
    """A causal observation changes the probability domain; a ready Day0 q is healthy."""

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
    _write_forecast_posterior(
        sd,
        city="Shanghai",
        target_date=target_date,
        metric="low",
        runtime_layer="live",
        computed_at=_now_iso(now, -1.0),
    )
    import src.control.live_health as live_health

    monkeypatch.setattr(
        live_health,
        "_authorized_day0_facts_for_health",
        lambda *args, **kwargs: (
            {("Shanghai", target_date, "low"): {"observation_time": _now_iso(now, -1.0)}},
            {},
        ),
    )
    monkeypatch.setattr(
        live_health,
        "_day0_remaining_authority_readiness",
        lambda *args, **kwargs: (
            True,
            "DAY0_REMAINING_AUTHORITY_READY",
            {"available_models": ("ecmwf_ifs", "icon_global", "ukmo_global_deterministic_10km")},
        ),
    )

    result = _posterior_starvation_surface(sd, now)

    assert result["ok"] is True
    assert result["starved_count"] == 0
    assert result["day0_authority_ready_count"] == 1
    assert result["day0_authority_ready_sample"][0]["authority"] == (
        "day0_remaining_day_global_probability_v1"
    )


def test_missing_day0_remaining_authority_remains_typed_after_starvation_threshold(
    tmp_path,
    monkeypatch,
):
    """A stale forecast scope with a Day0 fact exposes the actual missing authority."""

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
    _write_forecast_posterior(
        sd,
        city="Shanghai",
        target_date=target_date,
        metric="low",
        runtime_layer="live",
        computed_at=_now_iso(now, -1.0),
    )

    import src.control.live_health as live_health

    monkeypatch.setattr(
        live_health,
        "_authorized_day0_facts_for_health",
        lambda *args, **kwargs: (
            {("Shanghai", target_date, "low"): {"observation_time": _now_iso(now, -0.5)}},
            {},
        ),
    )
    monkeypatch.setattr(
        live_health,
        "_day0_remaining_authority_readiness",
        lambda *args, **kwargs: (
            False,
            "DAY0_COMPLETE_HOURLY_BUNDLE_UNAVAILABLE",
            {"expected_models": ("ecmwf_ifs", "icon_global")},
        ),
    )

    result = _posterior_starvation_surface(sd, now)

    assert result["ok"] is False
    assert result["starved_count"] == 1
    item = result["starved_sample"][0]
    assert item["authority"] == "day0_remaining_day_global_probability_v1"
    assert item["newest_blocked_reason"] == "DAY0_COMPLETE_HOURLY_BUNDLE_UNAVAILABLE"
    assert item["age_h"] == 1.0


def test_day0_readiness_reproduces_model_skew_and_remaining_window_contract(
    tmp_path,
    monkeypatch,
):
    """Health uses the same exact bundle contract as the money-path Day0 reader."""

    sd = tmp_path / "state"
    sd.mkdir()
    sqlite3.connect(sd / "zeus-forecasts.db").close()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    observed_at = _now_iso(now, -0.5)
    captured: dict[str, object] = {}

    import src.config
    import src.data.day0_hourly_vectors as vectors
    import src.control.live_health as live_health

    city = type("City", (), {"timezone": "Asia/Shanghai"})()
    monkeypatch.setattr(src.config, "runtime_cities_by_name", lambda: {"Shanghai": city})
    monkeypatch.setattr(
        vectors,
        "day0_hourly_models_for_city",
        lambda _city: ["ecmwf_ifs", "icon_global"],
    )

    def read_vectors(**kwargs):
        captured.update(kwargs)
        return [
            type("Vector", (), {"model": "ecmwf_ifs"})(),
            type("Vector", (), {"model": "icon_global"})(),
        ]

    monkeypatch.setattr(vectors, "read_freshest_day0_hourly_vectors", read_vectors)

    ready, reason, detail = live_health._day0_remaining_authority_readiness(
        sd,
        city="Shanghai",
        target_date="2026-07-17",
        event_payload={"observation_time": observed_at},
        now=now,
    )

    assert ready is True
    assert reason == "DAY0_REMAINING_AUTHORITY_READY"
    assert detail["available_models"] == ("ecmwf_ifs", "icon_global")
    assert captured["require_expected"] is True
    assert captured["max_bundle_skew_minutes"] == vectors.DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES
    assert captured["remaining_window_start"].isoformat() == observed_at
    assert captured["require_complete_remaining_window"] is True


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


def test_completed_east_of_utc_target_day_is_excluded(tmp_path):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 28, 18, 0, tzinfo=timezone.utc)

    _write_market_events(
        sd,
        city="Shanghai",
        target_date="2026-07-28",
        metric="low",
        token_id="tok-shanghai-low",
        created_at=_now_iso(now, -48.0),
    )
    _write_forecast_posterior(
        sd,
        city="Shanghai",
        target_date="2026-07-28",
        metric="low",
        runtime_layer="live",
        computed_at=_now_iso(now, -20.0),
    )

    result = _posterior_starvation_surface(sd, now)

    assert result["ok"] is True
    assert result["starved_count"] == 0


def test_utc_yesterday_still_current_western_local_day_is_checked(tmp_path):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc)

    _write_market_events(
        sd,
        city="Los Angeles",
        target_date="2026-07-17",
        metric="high",
        token_id="tok-los-angeles-high",
        created_at=_now_iso(now, -48.0),
    )
    _write_forecast_posterior(
        sd,
        city="Los Angeles",
        target_date="2026-07-17",
        metric="high",
        runtime_layer="live",
        computed_at=_now_iso(now, -20.0),
    )

    result = _posterior_starvation_surface(sd, now)

    assert result["ok"] is False
    assert result["starved_sample"][0]["city"] == "Los Angeles"


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


def test_blocked_reason_enrichment_scans_failed_directory_once(
    tmp_path,
    monkeypatch,
):
    sd = tmp_path / "state"
    sd.mkdir()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    target_date = "2026-07-17"
    for city in ("Shanghai", "Paris"):
        _write_market_events(
            sd,
            city=city,
            target_date=target_date,
            metric="high",
            token_id=f"tok-{city.lower()}-high",
            created_at=_now_iso(now, -48.0),
        )
        _write_forecast_posterior(
            sd,
            city=city,
            target_date=target_date,
            metric="high",
            runtime_layer="live",
            computed_at=_now_iso(now, -20.0),
        )
    failed_dir = sd / "replacement_forecast_live" / "failed"
    failed_dir.mkdir(parents=True)
    for city in ("Shanghai", "Paris"):
        for suffix, reason in (("000000", "older"), ("010000", f"newest-{city}")):
            (failed_dir / f"{city}.{target_date}.high.{suffix}.receipt.json").write_text(
                json.dumps({"stderr": reason})
            )

    import src.control.live_health as live_health

    real_scandir = live_health.os.scandir
    scan_count = 0

    def counted_scandir(path):
        nonlocal scan_count
        scan_count += 1
        return real_scandir(path)

    monkeypatch.setattr(live_health.os, "scandir", counted_scandir)

    result = _posterior_starvation_surface(sd, now)

    assert scan_count == 1
    assert {
        row["city"]: row["newest_blocked_reason"]
        for row in result["starved_sample"]
    } == {"Paris": "newest-Paris", "Shanghai": "newest-Shanghai"}


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
    import inspect

    from src.engine import event_reactor_adapter

    assert "posterior_starvation" not in inspect.getsource(
        event_reactor_adapter
    )


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
