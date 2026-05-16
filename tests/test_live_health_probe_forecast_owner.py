# Lifecycle: created=2026-05-15; last_reviewed=2026-05-15; last_reused=2026-05-15
# Purpose: Lock forecast-live as the canonical forecast owner for live health alerts.
# Reuse: Run when live_health_probe process/heartbeat classification or forecast-live launch ownership changes.
# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "live_health_probe.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("live_health_probe_under_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_forecast_live_owner_replaces_legacy_ingest_dead(tmp_path, monkeypatch, capsys):
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
    assert out.startswith("OK")
    assert "forecast_live=1" in out
    assert "legacy_ingest=0" in out
    assert "ingest_dead" not in out
    assert "forecast_live_dead" not in out


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
    assert "ingest_dead" not in out


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
    assert "ingest_dead" not in out


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
