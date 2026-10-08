# Created: 2026-10-08
# Last reused/audited: 2026-10-08
# Lifecycle: created=2026-10-08; last_reviewed=2026-10-08; last_reused=2026-10-08
# Purpose: Lock the fleet-wide live-posterior commit-rate alarm: threshold, window, branch naming, probe flag.
# Reuse: Run when live-health posterior surfaces, the replacement_forecast_live receipt dirs, or the probe flag set change.
# Authority basis: incidents 2026-10-06/07 (four outages, ~zero live posteriors for 1-5 h each, no alarm
#   distinguished them; posterior_starvation fires only at 12 h per-family staleness); operator calibration
#   threshold 40 per trailing 30 min.
"""Posterior commit-rate alarm antibody.

``_posterior_commit_rate_surface`` (src/control/live_health.py) counts live
``forecast_posteriors`` rows whose ``computed_at`` falls in the trailing 30
minutes.  Fewer than 40 is an outage (normal is 150-280); the alarm names its
branch (queued request files + top decline/failure reason codes) and is a
log-only surface, never an entry gate.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.control.live_health import (
    POSTERIOR_COMMIT_RATE_MIN_COUNT,
    POSTERIOR_COMMIT_RATE_WINDOW_MINUTES,
    _posterior_commit_rate_surface,
    compute_composite_live_health,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
PROBE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "live_health_probe.py"


def _write_posteriors(
    sd: Path,
    count: int,
    *,
    minutes_ago: float,
    runtime_layer: str | None = "live",
) -> None:
    conn = sqlite3.connect(sd / "zeus-forecasts.db")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS forecast_posteriors ("
            "city TEXT, target_date TEXT, temperature_metric TEXT, "
            "runtime_layer TEXT, computed_at TEXT)"
        )
        stamp = (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="microseconds")
        conn.executemany(
            "INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?)",
            [("Paris", "2026-10-09", "high", runtime_layer, stamp)] * count,
        )
        conn.commit()
    finally:
        conn.close()


def _state(tmp_path: Path) -> Path:
    sd = tmp_path / "state"
    sd.mkdir()
    return sd


def _receipt(sd: Path, directory: str, name: str, payload: dict, *, age_min: float = 1.0) -> Path:
    path = sd / "replacement_forecast_live" / directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    stamp = (NOW - timedelta(minutes=age_min)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_fires_at_ten_rows_and_names_the_branch(tmp_path, caplog):
    sd = _state(tmp_path)
    _write_posteriors(sd, 10, minutes_ago=5)
    for index in range(3):
        _receipt(
            sd,
            "blocked_latest",
            f"City{index}.2026-10-09.high.json",
            {
                "reason_codes": [
                    "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_BLOCKED_INPUT",
                    "REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET",
                    "FUSION_DECLINED:CURRENT_SHAPE_ENS_UNAVAILABLE",
                ]
            },
        )
    _receipt(
        sd,
        "blocked_latest",
        "Other.2026-10-09.low.json",
        {"reason_codes": ["READINESS_CERT_CYCLE_REGRESSION"]},
    )
    _receipt(
        sd,
        "seed_failed",
        "Z.2026-10-09.high.20261008T115000Z.x.json.receipt.json",
        {"status": "ERROR", "error": "database is locked", "failure_category": "ENVIRONMENT_RETRY"},
    )
    for index in range(4):
        (sd / "replacement_forecast_live" / "requests").mkdir(parents=True, exist_ok=True)
        (sd / "replacement_forecast_live" / "requests" / f"r{index}.json").write_text("{}")
    (sd / "replacement_forecast_live" / "requests" / ".r.json.stage.tmp").write_text("{}")

    with caplog.at_level(logging.ERROR, logger="src.control.live_health"):
        result = _posterior_commit_rate_surface(sd, NOW)

    assert result["ok"] is False
    assert result["evaluated"] is True
    assert result["count"] == 10
    assert result["window_min"] == POSTERIOR_COMMIT_RATE_WINDOW_MINUTES == 30
    assert result["threshold"] == POSTERIOR_COMMIT_RATE_MIN_COUNT == 40
    assert result["queued"] == 4
    assert result["issue"] == "POSTERIOR_COMMIT_RATE_COLLAPSE:count=10"
    assert result["top_reasons"] == [
        {"reason": "FUSION_DECLINED:CURRENT_SHAPE_ENS_UNAVAILABLE", "count": 3},
        {"reason": "READINESS_CERT_CYCLE_REGRESSION", "count": 1},
        {"reason": "SEED_FAILED:database_is_locked", "count": 1},
    ]
    lines = [r.getMessage() for r in caplog.records if "ZEUS_POSTERIOR_COMMIT_RATE_COLLAPSE" in r.getMessage()]
    assert lines == [
        "ZEUS_POSTERIOR_COMMIT_RATE_COLLAPSE count=10 window_min=30 threshold=40 queued=4 "
        "top_reasons=FUSION_DECLINED:CURRENT_SHAPE_ENS_UNAVAILABLE:3,"
        "READINESS_CERT_CYCLE_REGRESSION:1,SEED_FAILED:database_is_locked:1"
    ]
    assert all(r.levelno == logging.ERROR for r in caplog.records if "COLLAPSE" in r.getMessage())


def test_ok_at_one_hundred_rows_and_silent(tmp_path, caplog):
    sd = _state(tmp_path)
    _write_posteriors(sd, 100, minutes_ago=5)

    with caplog.at_level(logging.ERROR, logger="src.control.live_health"):
        result = _posterior_commit_rate_surface(sd, NOW)

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["count"] == 100
    assert "queued" not in result
    assert not [r for r in caplog.records if "COLLAPSE" in r.getMessage()]


def test_threshold_boundary_is_strictly_fewer_than_forty(tmp_path):
    sd = _state(tmp_path)
    _write_posteriors(sd, 39, minutes_ago=5)
    assert _posterior_commit_rate_surface(sd, NOW)["ok"] is False
    _write_posteriors(sd, 1, minutes_ago=5)
    assert _posterior_commit_rate_surface(sd, NOW)["ok"] is True


def test_rows_older_than_window_and_non_live_rows_are_not_counted(tmp_path):
    sd = _state(tmp_path)
    _write_posteriors(sd, 200, minutes_ago=31)
    _write_posteriors(sd, 200, minutes_ago=5, runtime_layer=None)
    _write_posteriors(sd, 5, minutes_ago=29)

    result = _posterior_commit_rate_surface(sd, NOW)

    assert result["ok"] is False
    assert result["count"] == 5


def test_reason_scan_ignores_receipts_older_than_window(tmp_path):
    sd = _state(tmp_path)
    _write_posteriors(sd, 0, minutes_ago=5)
    _receipt(sd, "blocked_latest", "A.2026-10-09.high.json", {"reason_codes": ["OLD_REASON"]}, age_min=90)
    _receipt(sd, "blocked_latest", "B.2026-10-09.high.json", {"reason_codes": ["FRESH_REASON"]}, age_min=2)

    result = _posterior_commit_rate_surface(sd, NOW)

    assert result["ok"] is False
    assert result["count"] == 0
    assert result["top_reasons"] == [{"reason": "FRESH_REASON", "count": 1}]
    assert result["queued"] is None


def test_unreadable_reason_receipts_never_block_the_alarm(tmp_path):
    sd = _state(tmp_path)
    _write_posteriors(sd, 3, minutes_ago=5)
    path = _receipt(sd, "blocked_latest", "A.2026-10-09.high.json", {}, age_min=1)
    path.write_text("{not json")

    result = _posterior_commit_rate_surface(sd, NOW)

    assert result["ok"] is False
    assert result["top_reasons"] == []


def test_missing_table_is_not_evaluated_and_ok(tmp_path):
    sd = _state(tmp_path)
    conn = sqlite3.connect(sd / "zeus-forecasts.db")
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()

    result = _posterior_commit_rate_surface(sd, NOW)

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["evaluated"] is False
    assert result["skip_reason"] == "FORECAST_POSTERIORS_COLUMNS_MISSING"


def test_missing_db_is_not_evaluated_and_ok(tmp_path):
    result = _posterior_commit_rate_surface(_state(tmp_path), NOW)

    assert result["ok"] is True
    assert result["evaluated"] is False
    assert result["skip_reason"] == "DB_MISSING"


def test_missing_columns_is_not_evaluated_and_ok(tmp_path):
    sd = _state(tmp_path)
    conn = sqlite3.connect(sd / "zeus-forecasts.db")
    conn.execute("CREATE TABLE forecast_posteriors (city TEXT)")
    conn.commit()
    conn.close()

    result = _posterior_commit_rate_surface(sd, NOW)

    assert result["ok"] is True
    assert result["evaluated"] is False


def test_composite_carries_surface_and_marks_it_failing(tmp_path):
    sd = _state(tmp_path)
    _write_posteriors(sd, 10, minutes_ago=5)

    result = compute_composite_live_health(state_dir=sd, now=NOW)

    assert result["surfaces"]["posterior_commit_rate"]["ok"] is False
    assert result["surfaces"]["posterior_commit_rate"]["count"] == 10
    assert "posterior_commit_rate" in result["failing_surfaces"]


def test_surface_is_not_an_entry_gate():
    import inspect

    from src.engine import event_reactor_adapter

    assert "posterior_commit_rate" not in inspect.getsource(event_reactor_adapter)


def _load_probe():
    spec = importlib.util.spec_from_file_location("live_health_probe_commit_rate", PROBE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _minimal_report(commit_rate: dict) -> dict:
    return {
        "hb": {"age_s": 1},
        "procs": {
            "daemon": [1],
            "data_ingest": [2],
            "forecast_live": [3],
            "riskguard": [4],
        },
        "forecast_live_hb": {"age_s": 1},
        "code_plane": {"status": "ok", "dirty": False, "matches_expected": True},
        "process_code": {"ok": True},
        "settlement_truth": {"ok": True},
        "posterior_commit_rate": commit_rate,
    }


def test_probe_flag_appears_on_collapse_and_not_otherwise():
    module = _load_probe()
    assert "posterior_commit_rate" in module.DIRECT_HEAD_LIVE_HEALTH_SURFACES

    collapsed = module._classify_alerts(
        _minimal_report({"ok": False, "evaluated": True, "count": 10, "issue": "POSTERIOR_COMMIT_RATE_COLLAPSE:count=10"}),
        0,
    )
    assert "posterior_commit_rate_collapse=10" in collapsed
    assert not [a for a in collapsed if a.startswith("LIVE_HEALTH_POSTERIOR_COMMIT_RATE")]

    healthy = module._classify_alerts(
        _minimal_report({"ok": True, "evaluated": True, "count": 150, "issue": None}),
        0,
    )
    assert not [a for a in healthy if "posterior_commit_rate" in a]

    skipped = module._classify_alerts(
        _minimal_report({"ok": True, "evaluated": False, "skip_reason": "DB_MISSING"}),
        0,
    )
    assert not [a for a in skipped if "posterior_commit_rate" in a]


def test_probe_flag_is_stripped_to_a_stable_state_signature():
    # live_health_monitor.sh strips "=<digits>" before comparing flag sets, so a
    # drifting count must not re-emit while the alarm stays raised.
    import re

    flags = "hb_stale=5s,posterior_commit_rate_collapse=10"
    other = "hb_stale=9s,posterior_commit_rate_collapse=12"
    strip = lambda text: re.sub(r"=[0-9]+s?", "", text)  # noqa: E731 - mirrors the sed in the monitor
    assert strip(flags) == strip(other) == "hb_stale,posterior_commit_rate_collapse"


def test_probe_direct_head_surface_reads_the_state_dir(tmp_path):
    module = _load_probe()
    root = tmp_path / "zeus"
    (root / "state").mkdir(parents=True)
    # Probe evaluates at wall-clock "now"; stamp rows relative to it.
    conn = sqlite3.connect(root / "state" / "zeus-forecasts.db")
    conn.execute(
        "CREATE TABLE forecast_posteriors ("
        "city TEXT, target_date TEXT, temperature_metric TEXT, runtime_layer TEXT, computed_at TEXT)"
    )
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(timespec="microseconds")
    conn.executemany(
        "INSERT INTO forecast_posteriors VALUES ('Paris','2026-10-09','high','live',?)",
        [(stamp,)] * 10,
    )
    conn.commit()
    conn.close()

    surfaces = module._direct_head_live_health_surfaces(root, status_summary={}, heartbeat=None)

    assert surfaces["posterior_commit_rate"]["ok"] is False
    assert surfaces["posterior_commit_rate"]["count"] == 10
    assert module._classify_alerts(
        _minimal_report(surfaces["posterior_commit_rate"]), 0
    ).count("posterior_commit_rate_collapse=10") == 1
