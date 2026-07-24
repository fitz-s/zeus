# Created: 2026-06-28
# Last reused/audited: 2026-07-02

from __future__ import annotations

import json
from types import SimpleNamespace

from scripts import zeus_status
from scripts.zeus_status import classify_block


def test_missing_live_input_remains_transient() -> None:
    assert classify_block("READINESS", "LIVE_INFERENCE_INPUTS_MISSING") == "transient"


def test_daemon_section_surfaces_missing_live_trading_with_stale_heartbeat(
    monkeypatch,
    tmp_path,
) -> None:
    stale_heartbeat = tmp_path / "daemon-heartbeat.json"
    stale_heartbeat.write_text(
        json.dumps(
            {
                "alive": True,
                "timestamp": "2026-07-02T16:23:41+00:00",
                "mode": "live",
                "pid": 88012,
                "process": "src.main",
            }
        )
    )

    def fake_run(args, **kwargs):
        if args[:2] == ["launchctl", "list"]:
            return SimpleNamespace(
                returncode=0,
                stdout="12091\t0\tcom.zeus.forecast-live\n",
                stderr="",
            )
        if args[:2] == ["ps", "-p"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {args!r}")

    monkeypatch.setattr(zeus_status, "STATE", str(tmp_path))
    monkeypatch.setattr(zeus_status.subprocess, "run", fake_run)
    monkeypatch.setattr(zeus_status, "_current_git_head", lambda: "currenthead")
    monkeypatch.setattr(
        zeus_status,
        "_now",
        lambda: zeus_status.datetime.fromisoformat("2026-07-02T18:26:59+00:00"),
    )

    data = zeus_status.section_daemons()
    live = next(row for row in data["rows"] if row["label"] == "live-trading")

    assert live["ok"] is False
    assert live["alive"] is False
    assert "missing_from_launchctl" in live["issues"]
    assert "pid_missing" in live["issues"]
    assert "heartbeat_stale" in live["issues"]


def test_daemon_section_surfaces_heartbeat_git_head_mismatch(
    monkeypatch,
    tmp_path,
) -> None:
    heartbeat = tmp_path / "forecast-live-heartbeat.json"
    heartbeat.write_text(
        json.dumps(
            {
                "alive": True,
                "timestamp": "2026-07-02T18:26:50+00:00",
                "daemon": "forecast-live",
                "git_head": "oldhead1",
            }
        )
    )

    def fake_run(args, **kwargs):
        if args[:2] == ["launchctl", "list"]:
            return SimpleNamespace(
                returncode=0,
                stdout="12091\t0\tcom.zeus.forecast-live\n",
                stderr="",
            )
        if args[:2] == ["ps", "-p"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {args!r}")

    monkeypatch.setattr(zeus_status, "STATE", str(tmp_path))
    monkeypatch.setattr(zeus_status.subprocess, "run", fake_run)
    monkeypatch.setattr(zeus_status, "_current_git_head", lambda: "newhead2")
    monkeypatch.setattr(
        zeus_status,
        "_now",
        lambda: zeus_status.datetime.fromisoformat("2026-07-02T18:26:59+00:00"),
    )

    data = zeus_status.section_daemons()
    forecast = next(row for row in data["rows"] if row["label"] == "forecast-live")

    assert forecast["ok"] is False
    assert forecast["heartbeat"]["issue"] == "heartbeat_git_head_mismatch"
    assert "heartbeat_git_head_mismatch" in forecast["issues"]
