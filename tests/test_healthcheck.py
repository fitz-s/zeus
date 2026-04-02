from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from scripts import healthcheck


def _write_risk_state(path, *, checked_at=None):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS risk_state (id INTEGER PRIMARY KEY, level TEXT NOT NULL, checked_at TEXT NOT NULL)"
    )
    conn.execute("DELETE FROM risk_state")
    conn.execute(
        "INSERT INTO risk_state (level, checked_at) VALUES (?, ?)",
        ("GREEN", checked_at or datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def test_healthcheck_uses_mode_qualified_status_and_reports_healthy(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-paper.json"
    risk_path = tmp_path / "risk_state-paper.db"
    status_path.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "risk": {"level": "GREEN"},
        "portfolio": {"open_positions": 1, "total_exposure_usd": 6.99},
        "cycle": {"entries_blocked_reason": "risk_level=ORANGE"},
        "execution": {"overall": {"entry_rejected": 2}},
        "strategy": {"center_buy": {"open_positions": 1}},
    }))
    _write_risk_state(risk_path)

    monkeypatch.setenv("ZEUS_MODE", "paper")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.paper-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["mode"] == "paper"
    assert result["daemon_alive"] is True
    assert result["riskguard_alive"] is True
    assert result["status_path"] == str(status_path)
    assert result["status_fresh"] is True
    assert result["riskguard_fresh"] is True
    assert result["entries_blocked_reason"] == "risk_level=ORANGE"
    assert result["execution_summary"]["entry_rejected"] == 2
    assert result["strategy_summary"]["center_buy"]["open_positions"] == 1
    assert result["healthy"] is True
    assert healthcheck.exit_code_for(result) == 0


def test_healthcheck_parses_launchctl_kv_output(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-paper.json"
    risk_path = tmp_path / "risk_state-paper.db"
    status_path.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "risk": {"level": "GREEN"},
        "portfolio": {"open_positions": 1, "total_exposure_usd": 6.99},
    }))
    _write_risk_state(risk_path)

    monkeypatch.setenv("ZEUS_MODE", "paper")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)

    class _Result:
        returncode = 0
        stdout = '{\n\t"Label" = "com.zeus.paper-trading";\n\t"PID" = 59087;\n\t"LastExitStatus" = 15;\n};\n'

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["pid"] == 59087
    assert result["daemon_alive"] is True
    assert result["riskguard_alive"] is True
    assert result["healthy"] is True


def test_healthcheck_is_not_healthy_when_daemon_is_dead(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-live.json"
    risk_path = tmp_path / "risk_state-live.db"
    status_path.write_text(json.dumps({
        "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        "risk": {"level": "GREEN"},
        "portfolio": {"open_positions": 0, "total_exposure_usd": 0.0},
    }))
    _write_risk_state(risk_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)

    class _Result:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["mode"] == "live"
    assert result["daemon_alive"] is False
    assert result["status_fresh"] is True
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_is_not_healthy_when_riskguard_is_missing(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-paper.json"
    risk_path = tmp_path / "risk_state-paper.db"
    status_path.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "risk": {"level": "GREEN"},
        "portfolio": {"open_positions": 1, "total_exposure_usd": 6.99},
    }))
    _write_risk_state(risk_path, checked_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())

    monkeypatch.setenv("ZEUS_MODE", "paper")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)

    def _run(cmd, *args, **kwargs):
        class _Result:
            returncode = 0 if cmd[-1] == "com.zeus.paper-trading" else 1
            stdout = "123\t0\tcom.zeus.paper-trading\n" if cmd[-1] == "com.zeus.paper-trading" else ""
        return _Result()

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = healthcheck.check()

    assert result["daemon_alive"] is True
    assert result["riskguard_alive"] is False
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1
