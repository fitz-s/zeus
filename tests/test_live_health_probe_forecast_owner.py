# Lifecycle: created=2026-05-15; last_reviewed=2026-05-18; last_reused=2026-05-18
# Purpose: Lock forecast-live as the canonical forecast owner for live health alerts.
# Reuse: Run when live_health_probe process/heartbeat classification or forecast-live launch ownership changes.
# Created: 2026-05-15
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md; docs/operations/task_2026-05-16_live_continuous_run_package/LIVE_CONTINUOUS_RUN_PACKAGE_PLAN.md Phase C; 2026-05-17 volatile runtime-artifact code-plane contract.

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "live_health_probe.py"
FORECAST_READY_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_forecast_live_ready.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("live_health_probe_under_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_forecast_ready_module():
    module_name = "check_forecast_live_ready_under_test"
    spec = importlib.util.spec_from_file_location(module_name, FORECAST_READY_SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_live_probe_loaded_code_surface_includes_recovery_and_m5_paths():
    module = _load_module()
    daemon_paths = set(module.PROCESS_CODE_SURFACES["daemon"])

    assert "src/engine/evaluator.py" in daemon_paths
    assert "src/contracts/executable_market_snapshot_v2.py" in daemon_paths
    assert "src/contracts/execution_intent.py" in daemon_paths
    assert "src/data/market_scanner.py" in daemon_paths
    assert "src/control/ws_gap_guard.py" in daemon_paths
    assert "src/execution/command_recovery.py" in daemon_paths
    assert "src/execution/exchange_reconcile.py" in daemon_paths
    assert "src/data/polymarket_client.py" in daemon_paths


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _healthy_state(root: Path) -> None:
    _write_json(root / "state" / "daemon-heartbeat.json", {"alive": True})
    _write_json(
        root / "state" / "forecast-live-heartbeat.json",
        {"alive": True, "status": "alive"},
    )
    _write_json(
        root / "state" / "status_summary.json",
        {
            "cycle": {
                "mode": "opening_hunt",
                "risk_level": "GREEN",
                "ws_user_channel": {"connected": True, "subscription_state": "SUBSCRIBED"},
                "block_registry": [],
            },
            "risk": {"level": "GREEN"},
            "lifecycle_funnel": {"counts": {"evaluated": 1, "selected": 0, "filled": 0}},
            "execution_capability": {
                "entry": {
                    "status": "requires_intent",
                    "global_allow_submit": True,
                    "live_action_authorized": False,
                }
            },
        },
    )


def _configure(
    module,
    monkeypatch,
    root: Path,
    snapshot: Path,
    alive_by_pattern: dict[str, list[int]],
    env_by_pid: dict[int, dict[str, str]] | None = None,
) -> None:
    monkeypatch.setattr(module, "ROOT", str(root))
    monkeypatch.setattr(module, "SNAPSHOT_FILE", str(snapshot))
    env_by_pid = env_by_pid or {}

    def fake_alive(pattern: str) -> list[int]:
        return list(alive_by_pattern.get(pattern, []))

    def fake_process_env(pid: int) -> dict[str, str]:
        return dict(env_by_pid.get(pid, {}))

    monkeypatch.setattr(module, "_alive", fake_alive)
    monkeypatch.setattr(module, "_process_env", fake_process_env)
    monkeypatch.setattr(
        module,
        "_git_runtime_identity",
        lambda root_arg: {
            "status": "ok",
            "repo": str(root_arg),
            "head": "expected-commit",
            "branch": "main",
            "dirty": False,
            "expected_ref": "origin/main",
            "expected_commit": "expected-commit",
            "expected_error": None,
            "matches_expected": True,
        },
    )
    monkeypatch.setattr(
        module,
        "_process_loaded_code_status",
        lambda procs, root_arg: {"ok": True, "issue": None, "stale": [], "unattested": [], "items": []},
    )
    monkeypatch.setattr(
        module,
        "_settlement_truth_status",
        lambda root_arg: {
            "ok": True,
            "issue": None,
            "count": 1,
            "max_settled_at": "2026-05-16T00:00:00+00:00",
            "age_s": 1,
        },
    )


def test_data_ingest_support_daemon_required_even_when_forecast_live_owner_alive(
    tmp_path, monkeypatch, capsys
):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [202],
            "src.ingest_main": [],
            "src.riskguard": [303],
        },
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("ALERT")
    assert "forecast_live=1" in out
    assert "data_ingest=0" in out
    assert "legacy_ingest=0" in out
    assert "data_ingest_dead" in out
    assert "forecast_live_dead" not in out


def test_live_probe_alerts_on_degraded_business_plane_composite(
    tmp_path, monkeypatch, capsys
):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    _write_json(
        root / "state" / "live_health_composite.json",
        {
            "healthy": False,
            "status": "DEGRADED",
            "failing_surfaces": ["business_plane"],
            "surfaces": {
                "business_plane": {
                    "ok": False,
                    "issue": "CYCLE_IN_PROGRESS_NO_COMPLETED_AT",
                }
            },
        },
    )
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [202],
            "src.ingest_main": [404],
            "src.riskguard": [303],
        },
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("ALERT")
    assert "LIVE_HEALTH_BUSINESS_PLANE=CYCLE_IN_PROGRESS_NO_COMPLETED_AT" in out
    assert "flags=all_healthy" not in out


def test_live_probe_alerts_when_status_summary_process_pid_is_stale(
    tmp_path, monkeypatch, capsys
):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    _write_json(
        root / "state" / "status_summary.json",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "process": {"pid": 999, "mode": "live"},
            "cycle": {
                "mode": "opening_hunt",
                "risk_level": "GREEN",
                "ws_user_channel": {"connected": True, "subscription_state": "SUBSCRIBED"},
                "block_registry": [],
            },
            "risk": {"level": "GREEN"},
            "lifecycle_funnel": {"counts": {"evaluated": 1, "selected": 0, "filled": 0}},
            "execution_capability": {
                "entry": {
                    "status": "requires_intent",
                    "global_allow_submit": True,
                    "live_action_authorized": False,
                }
            },
        },
    )
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [202],
            "src.ingest_main": [404],
            "src.riskguard": [303],
        },
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("ALERT")
    assert "STATUS_SUMMARY_PROCESS_PID_MISMATCH" in out
    assert "flags=all_healthy" not in out


def test_alive_matches_python_module_not_shell_text(monkeypatch):
    module = _load_module()

    def fake_run(*args, **kwargs):
        assert args[0] == ["ps", "-axo", "pid=,command="]
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout=(
                "101 /usr/bin/python -m src.main\n"
                "202 /usr/bin/python -m src.ingest.forecast_live_daemon\n"
                "303 /bin/zsh -lc rg src.ingest_main\n"
                "404 /usr/bin/python -m src.ingest_main\n"
                "505 /usr/bin/python -m src.riskguard.riskguard\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module._alive("src.main") == [101]
    assert module._alive("src.ingest.forecast_live_daemon") == [202]
    assert module._alive("src.ingest_main") == [404]
    assert module._alive("src.riskguard") == [505]


def test_forecast_ready_process_check_matches_launchd_python_app_module_not_shell_text(monkeypatch):
    module = _load_forecast_ready_module()

    def fake_run(*args, **kwargs):
        assert args[0] == ["ps", "-axo", "pid=,command="]
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout=(
                "202 /opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python -m src.ingest.forecast_live_daemon\n"
                "303 /bin/zsh -lc rg src.ingest.forecast_live_daemon\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    ok, blockers, result = module._process_check("python -m src.ingest.forecast_live_daemon")

    assert ok is True
    assert blockers == []
    assert result.status == "PASS"
    assert result.metadata["module_pattern"] == "src.ingest.forecast_live_daemon"
    assert len(result.metadata["matches"]) == 1
    assert result.metadata["matches"][0].startswith("202 ")


def test_forecast_ready_process_check_rejects_shell_text_without_python_module(monkeypatch):
    module = _load_forecast_ready_module()

    def fake_run(*args, **kwargs):
        assert args[0] == ["ps", "-axo", "pid=,command="]
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="303 /bin/zsh -lc rg src.ingest.forecast_live_daemon\n",
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    ok, blockers, result = module._process_check("python -m src.ingest.forecast_live_daemon")

    assert ok is False
    assert blockers == ["FORECAST_LIVE_PROCESS_MISSING"]
    assert result.status == "BLOCKED"
    assert result.metadata["module_pattern"] == "src.ingest.forecast_live_daemon"


def _forecast_ready_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE job_run (
            job_run_id TEXT,
            job_name TEXT,
            status TEXT,
            scheduled_for TEXT,
            source_run_id TEXT,
            release_calendar_key TEXT,
            rows_written INTEGER,
            recorded_at TEXT
        );
        CREATE TABLE source_run (
            source_run_id TEXT,
            source_id TEXT,
            track TEXT,
            status TEXT,
            completeness_status TEXT,
            source_cycle_time TEXT,
            recorded_at TEXT
        );
        CREATE TABLE source_run_coverage (
            source_id TEXT,
            source_transport TEXT,
            source_run_id TEXT,
            track TEXT,
            temperature_metric TEXT,
            completeness_status TEXT,
            readiness_status TEXT,
            expires_at TEXT,
            computed_at TEXT,
            recorded_at TEXT
        );
        CREATE TABLE readiness_state (
            source_id TEXT,
            source_run_id TEXT,
            track TEXT,
            temperature_metric TEXT,
            strategy_key TEXT,
            status TEXT,
            expires_at TEXT,
            dependency_json TEXT,
            computed_at TEXT,
            recorded_at TEXT
        );
        """
    )


def test_forecast_ready_uses_latest_safe_cycle_not_arbitrary_latest_blocked_row():
    module = _load_forecast_ready_module()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _forecast_ready_schema(conn)
    now = datetime(2026, 5, 17, 20, 10, tzinfo=timezone.utc)
    source_run_id = "ecmwf_open_data:mx2t6_high:2026-05-17T12Z"

    conn.execute(
        "INSERT INTO job_run VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "job-current",
            "forecast_live_opendata_mx2t6_high",
            "PARTIAL",
            "2026-05-17T12:00:00+00:00",
            source_run_id,
            "ecmwf_open_data:mx2t6_high:full",
            364,
            "2026-05-17T20:10:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO source_run VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            source_run_id,
            "ecmwf_open_data",
            "mx2t6_high_full_horizon",
            "PARTIAL",
            "PARTIAL",
            "2026-05-17T12:00:00+00:00",
            "2026-05-17T20:10:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO source_run_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ecmwf_open_data",
            "ensemble_snapshots_v2_db_reader",
            source_run_id,
            "mx2t6_high_full_horizon",
            "high",
            "COMPLETE",
            "LIVE_ELIGIBLE",
            "2026-05-18T20:10:00+00:00",
            "2026-05-17T20:10:00+00:00",
            "2026-05-17T20:10:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO readiness_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ecmwf_open_data",
            source_run_id,
            "mx2t6_high_full_horizon",
            "high",
            "producer_readiness",
            "LIVE_ELIGIBLE",
            "2026-05-18T20:10:00+00:00",
            json.dumps({"source_run_id": source_run_id}),
            "2026-05-17T20:10:00+00:00",
            "2026-05-17T20:10:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO readiness_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ecmwf_open_data",
            "ecmwf_open_data:mx2t6_high:2026-05-16T12Z",
            "mx2t6_high_full_horizon",
            "high",
            "producer_readiness",
            "BLOCKED",
            None,
            "{}",
            "2026-05-17T20:11:00+00:00",
            "2026-05-17T20:11:00+00:00",
        ),
    )

    report = module._evaluate_track(conn, module.TRACKS[0], now)

    assert report.ready is True
    assert report.blockers == []
    assert report.job_run["source_run_id"] == source_run_id
    assert report.readiness_summary["live_eligible_current_count"] == 1


def test_forecast_ready_blocks_when_latest_safe_cycle_was_not_journaled():
    module = _load_forecast_ready_module()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _forecast_ready_schema(conn)
    now = datetime(2026, 5, 17, 20, 10, tzinfo=timezone.utc)

    conn.execute(
        "INSERT INTO job_run VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "job-stale",
            "forecast_live_opendata_mx2t6_high",
            "PARTIAL",
            "2026-05-16T12:00:00+00:00",
            "ecmwf_open_data:mx2t6_high:2026-05-16T12Z",
            "ecmwf_open_data:mx2t6_high:full",
            364,
            "2026-05-17T07:30:00+00:00",
        ),
    )

    report = module._evaluate_track(conn, module.TRACKS[0], now)

    assert report.ready is False
    assert "HIGH_LATEST_SAFE_SOURCE_RUN_MISSING:ecmwf_open_data:mx2t6_high:2026-05-17T12Z" in report.blockers


def test_missing_forecast_live_owner_is_actionable_without_legacy_ingest_dead(tmp_path, monkeypatch, capsys):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [],
            "src.ingest_main": [],
            "src.riskguard": [303],
        },
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("ALERT")
    assert "forecast_live_dead" in out


def test_stale_forecast_live_heartbeat_is_actionable(tmp_path, monkeypatch, capsys):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    forecast_hb = root / "state" / "forecast-live-heartbeat.json"
    old = forecast_hb.stat().st_mtime - module.FORECAST_LIVE_STALE_SECONDS - 30
    os.utime(forecast_hb, (old, old))
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [202],
            "src.ingest_main": [],
            "src.riskguard": [303],
        },
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("ALERT")
    assert "forecast_live_stale=" in out


def test_forecast_live_heartbeat_age_prefers_payload_written_at(
    tmp_path, monkeypatch, capsys
):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    forecast_hb = root / "state" / "forecast-live-heartbeat.json"
    _write_json(
        forecast_hb,
        {
            "alive": True,
            "status": "alive",
            "written_at": "2000-01-01T00:00:00+00:00",
        },
    )
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [202],
            "src.ingest_main": [404],
            "src.riskguard": [303],
        },
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("ALERT")
    assert "forecast_live_stale=" in out


def test_forecast_live_heartbeat_age_falls_back_to_mtime_when_payload_lacks_timestamp(
    tmp_path, monkeypatch
):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    forecast_hb = root / "state" / "forecast-live-heartbeat.json"
    _write_json(forecast_hb, {"alive": True, "status": "alive"})

    payload = module._load_json(str(forecast_hb))
    age, source = module._heartbeat_payload_age(payload)

    assert age is None
    assert source is None


def test_forecast_live_future_payload_timestamp_falls_back_to_mtime():
    module = _load_module()

    age, source = module._heartbeat_payload_age(
        {"written_at": "2026-05-18T12:01:00+00:00"},
        now_epoch=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc).timestamp(),
    )

    assert age is None
    assert source is None


def test_parse_iso_epoch_interprets_legacy_naive_timestamp_as_utc():
    module = _load_module()

    naive = module._parse_iso_epoch("2026-05-18T12:00:00")
    aware = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc).timestamp()

    assert naive == aware


def test_legacy_ingest_opendata_owner_is_actionable(tmp_path, monkeypatch, capsys):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [202],
            "src.ingest_main": [404],
            "src.riskguard": [303],
        },
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("ALERT")
    assert "legacy_ingest=1" in out
    assert "legacy_ingest_opendata_owner_present" in out


def test_legacy_ingest_without_opendata_ownership_is_observed_not_actionable(
    tmp_path, monkeypatch, capsys
):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [202],
            "src.ingest_main": [404],
            "src.riskguard": [303],
        },
        {404: {"ZEUS_FORECAST_LIVE_OWNER": "forecast_live"}},
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("OK")
    assert "legacy_ingest=1" in out
    assert "legacy_ingest_opendata_owner_present" not in out


def test_entry_blocked_is_actionable_even_when_daemons_are_alive(tmp_path, monkeypatch, capsys):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    status_path = root / "state" / "status_summary.json"
    payload = json.loads(status_path.read_text())
    payload["cycle"]["block_registry"] = [
        {"name": "ws_gap_guard_allow_submit", "state": "blocking", "blocking_reason": "ws_gap"}
    ]
    payload["execution_capability"]["entry"]["status"] = "blocked"
    status_path.write_text(json.dumps(payload), encoding="utf-8")
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [202],
            "src.ingest_main": [404],
            "src.riskguard": [303],
        },
        {404: {"ZEUS_FORECAST_LIVE_OWNER": "forecast_live"}},
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("ALERT")
    assert "entry=blocked" in out
    assert "blocking_gates=1" in out
    assert "entry_blocked" in out


def test_process_loaded_code_stale_is_actionable(tmp_path, monkeypatch, capsys):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [202],
            "src.ingest_main": [404],
            "src.riskguard": [303],
        },
        {404: {"ZEUS_FORECAST_LIVE_OWNER": "forecast_live"}},
    )
    monkeypatch.setattr(
        module,
        "_process_loaded_code_status",
        lambda procs, root_arg: {
            "ok": False,
            "issue": "PROCESS_LOADED_CODE_STALE",
            "stale": [{"process": "forecast_live", "pid": 202}],
            "unattested": [],
            "items": [],
        },
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("ALERT")
    assert "PROCESS_LOADED_CODE_STALE" in out


def test_settlement_truth_stale_is_actionable(tmp_path, monkeypatch, capsys):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [202],
            "src.ingest_main": [404],
            "src.riskguard": [303],
        },
        {404: {"ZEUS_FORECAST_LIVE_OWNER": "forecast_live"}},
    )
    monkeypatch.setattr(
        module,
        "_settlement_truth_status",
        lambda root_arg: {
            "ok": False,
            "issue": "SETTLEMENT_TRUTH_STALE",
            "count": 10,
            "max_settled_at": "2026-05-11T19:59:13+00:00",
            "age_s": 500000,
        },
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("ALERT")
    assert "SETTLEMENT_TRUTH_STALE" in out


def test_code_plane_drift_is_actionable(tmp_path, monkeypatch, capsys):
    module = _load_module()
    root = tmp_path / "zeus"
    _healthy_state(root)
    _configure(
        module,
        monkeypatch,
        root,
        tmp_path / "snapshot.json",
        {
            "src.main": [101],
            "src.ingest.forecast_live_daemon": [202],
            "src.ingest_main": [],
            "src.riskguard": [303],
        },
    )
    monkeypatch.setattr(
        module,
        "_git_runtime_identity",
        lambda root_arg: {
            "status": "ok",
            "repo": str(root_arg),
            "head": "running-commit",
            "branch": "deploy/live",
            "dirty": True,
            "expected_ref": "origin/main",
            "expected_commit": "main-commit",
            "expected_error": None,
            "matches_expected": False,
        },
    )

    module.main()

    out = capsys.readouterr().out
    assert out.startswith("ALERT")
    assert "LIVE_CODE_PLANE_DRIFT" in out
    assert "commit=running-commit" in out
    assert "expected=main-commit" in out
    assert "dirty=True" in out


def test_git_runtime_identity_uses_expected_commit_env(tmp_path, monkeypatch):
    module = _load_module()
    root = tmp_path / "zeus"
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        git_args = args[3:]
        if git_args == ["rev-parse", "HEAD"]:
            stdout = "abc123\n"
        elif git_args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            stdout = "main\n"
        elif git_args == ["status", "--porcelain"]:
            stdout = ""
        else:
            raise AssertionError(f"unexpected git args: {git_args!r}")
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setenv("ZEUS_LIVE_EXPECTED_COMMIT", "abc123")

    identity = module._git_runtime_identity(str(root))

    assert identity["status"] == "ok"
    assert identity["head"] == "abc123"
    assert identity["expected_commit"] == "abc123"
    assert identity["matches_expected"] is True
    assert identity["dirty"] is False
    assert ["git", "-C", str(root), "rev-parse", "origin/main"] not in calls


def test_git_runtime_identity_ignores_station_migration_timestamp_artifact(tmp_path, monkeypatch):
    module = _load_module()
    root = tmp_path / "zeus"

    def fake_run(args, **kwargs):
        git_args = args[3:]
        if git_args == ["rev-parse", "HEAD"]:
            stdout = "abc123\n"
        elif git_args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            stdout = "main\n"
        elif git_args == ["status", "--porcelain"]:
            stdout = " M station_migration_alerts.json\n"
        else:
            raise AssertionError(f"unexpected git args: {git_args!r}")
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setenv("ZEUS_LIVE_EXPECTED_COMMIT", "abc123")

    identity = module._git_runtime_identity(str(root))

    assert identity["dirty"] is False
    assert identity["dirty_paths"] == []
    assert identity["ignored_dirty_paths"] == ["station_migration_alerts.json"]


def test_git_runtime_identity_ignores_state_station_migration_timestamp_artifact(tmp_path, monkeypatch):
    module = _load_module()
    root = tmp_path / "zeus"

    def fake_run(args, **kwargs):
        git_args = args[3:]
        if git_args == ["rev-parse", "HEAD"]:
            stdout = "abc123\n"
        elif git_args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            stdout = "main\n"
        elif git_args == ["status", "--porcelain"]:
            stdout = " M state/station_migration_alerts.json\n"
        else:
            raise AssertionError(f"unexpected git args: {git_args!r}")
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setenv("ZEUS_LIVE_EXPECTED_COMMIT", "abc123")

    identity = module._git_runtime_identity(str(root))

    assert identity["dirty"] is False
    assert identity["dirty_paths"] == []
    assert identity["ignored_dirty_paths"] == ["state/station_migration_alerts.json"]


def test_git_runtime_identity_still_flags_material_dirty_path(tmp_path, monkeypatch):
    module = _load_module()
    root = tmp_path / "zeus"

    def fake_run(args, **kwargs):
        git_args = args[3:]
        if git_args == ["rev-parse", "HEAD"]:
            stdout = "abc123\n"
        elif git_args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            stdout = "main\n"
        elif git_args == ["status", "--porcelain"]:
            stdout = " M station_migration_alerts.json\n M src/main.py\n"
        else:
            raise AssertionError(f"unexpected git args: {git_args!r}")
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setenv("ZEUS_LIVE_EXPECTED_COMMIT", "abc123")

    identity = module._git_runtime_identity(str(root))

    assert identity["dirty"] is True
    assert identity["dirty_paths"] == ["src/main.py"]
    assert identity["ignored_dirty_paths"] == ["station_migration_alerts.json"]
