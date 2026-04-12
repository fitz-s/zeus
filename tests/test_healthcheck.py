from __future__ import annotations
import pytest

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from scripts import healthcheck


def _write_risk_state(path, *, checked_at=None, details=None):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS risk_state (id INTEGER PRIMARY KEY, level TEXT NOT NULL, details_json TEXT, checked_at TEXT NOT NULL)"
    )
    conn.execute("DELETE FROM risk_state")
    if details is None:
        details = {
            "execution_quality_level": "GREEN",
            "strategy_signal_level": "GREEN",
            "recommended_controls": [],
            "recommended_strategy_gates": [],
        }
    conn.execute(
        "INSERT INTO risk_state (level, details_json, checked_at) VALUES (?, ?, ?)",
        ("GREEN", json.dumps(details), checked_at or datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def _status_payload(*, timestamp=None, risk=None, portfolio=None, cycle=None, execution=None, strategy=None, learning=None, control=None, runtime=None):
    return {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "risk": risk or {"level": "GREEN", "details": {
            "execution_quality_level": "GREEN",
            "strategy_signal_level": "GREEN",
            "recommended_controls": [],
            "recommended_strategy_gates": [],
        }},
        "portfolio": portfolio or {"open_positions": 0, "total_exposure_usd": 0.0},
        "cycle": cycle or {},
        "execution": execution or {"overall": {"entry_rejected": 0}},
        "strategy": strategy or {},
        "learning": learning or {"no_trade_stage_counts": {}},
        "control": control or {
            "entries_paused": False,
            "strategy_gates": {},
            "recommended_but_not_gated": [],
            "gated_but_not_recommended": [],
            "recommended_controls_not_applied": [],
            "recommended_auto_commands": [],
            "review_required_commands": [],
            "recommended_commands": [],
        },
        "runtime": runtime or {"unverified_entries": 0, "day0_positions": 0},
        "truth": {"source_path": "status.json", "deprecated": False},
    }


def _write_no_trade_artifact(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS decision_log (id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, artifact_json TEXT NOT NULL, timestamp TEXT NOT NULL, env TEXT NOT NULL DEFAULT 'paper')"
    )
    artifact = {
        "mode": "opening_hunt",
        "started_at": "2026-04-02T00:00:00Z",
        "completed_at": "2026-04-02T00:01:00Z",
        "no_trade_cases": [
            {
                "decision_id": "d1",
                "city": "NYC",
                "target_date": "2026-04-02",
                "range_label": "39-40°F",
                "direction": "buy_yes",
                "rejection_stage": "EDGE_INSUFFICIENT",
                "rejection_reasons": ["small"],
            },
            {
                "decision_id": "d2",
                "city": "NYC",
                "target_date": "2026-04-02",
                "range_label": "41-42°F",
                "direction": "buy_yes",
                "rejection_stage": "RISK_REJECTED",
                "rejection_reasons": ["risk"],
            },
        ],
    }
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        ("opening_hunt", "2026-04-02T00:00:00Z", "2026-04-02T00:01:00Z", json.dumps(artifact), datetime.now(timezone.utc).isoformat(), "paper"),
    )
    conn.commit()
    conn.close()


@pytest.mark.skip(reason="Phase2: paper mode removed")
def test_healthcheck_uses_mode_qualified_status_and_reports_healthy(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-paper.json"
    risk_path = tmp_path / "risk_state-paper.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        risk={"level": "GREEN", "details": {
            "execution_quality_level": "GREEN",
            "strategy_signal_level": "GREEN",
            "recommended_controls": ["tighten_risk"],
            "recommended_strategy_gates": ["center_buy"],
        }},
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
        cycle={"entries_blocked_reason": "risk_level=ORANGE"},
        execution={"overall": {"entry_rejected": 2}},
        strategy={"center_buy": {"open_positions": 1}},
        learning={"no_trade_stage_counts": {"EDGE_INSUFFICIENT": 1}},
        control={
            "entries_paused": True,
            "strategy_gates": {"opening_inertia": False},
            "recommended_but_not_gated": ["center_buy"],
            "gated_but_not_recommended": [],
            "recommended_controls_not_applied": [],
            "recommended_auto_commands": [],
            "review_required_commands": [
                {"command": "set_strategy_gate", "strategy": "center_buy", "enabled": False}
            ],
            "recommended_commands": [
                {"command": "set_strategy_gate", "strategy": "center_buy", "enabled": False}
            ],
        },
        runtime={"unverified_entries": 1, "day0_positions": 2},
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "paper")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

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
    assert result["status_contract_valid"] is True
    assert result["riskguard_fresh"] is True
    assert result["riskguard_contract_valid"] is True
    assert result["entries_blocked_reason"] == "risk_level=ORANGE"
    assert result["execution_summary"]["entry_rejected"] == 2
    assert result["strategy_summary"]["center_buy"]["open_positions"] == 1
    assert result["learning_summary"]["no_trade_stage_counts"]["EDGE_INSUFFICIENT"] == 1
    assert result["control_state"]["entries_paused"] is True
    assert result["runtime_summary"]["unverified_entries"] == 1
    assert result["risk_details"]["recommended_controls"] == ["tighten_risk"]
    assert result["recommended_auto_commands"] == []
    assert result["review_required_commands"] == [
        {"command": "set_strategy_gate", "strategy": "center_buy", "enabled": False}
    ]
    assert result["recommended_commands"] == [
        {"command": "set_strategy_gate", "strategy": "center_buy", "enabled": False}
    ]
    assert result["auto_action_available"] is False
    assert result["recent_no_trade_stage_counts"]["EDGE_INSUFFICIENT"] == 1
    assert result["healthy"] is True
    assert healthcheck.exit_code_for(result) == 0


def test_healthcheck_parses_launchctl_kv_output(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-paper.json"
    risk_path = tmp_path / "risk_state-paper.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
    )))
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


def test_healthcheck_falls_back_to_launchctl_print_when_list_fails(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-paper.json"
    risk_path = tmp_path / "risk_state-paper.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
    )))
    _write_risk_state(risk_path)

    monkeypatch.setenv("ZEUS_MODE", "paper")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)

    class _Result:
        def __init__(self, returncode, stdout=""):
            self.returncode = returncode
            self.stdout = stdout

    def _run(cmd, *args, **kwargs):
        if cmd[:2] == ["launchctl", "list"]:
            return _Result(1, "")
        if cmd[:2] == ["launchctl", "print"]:
            return _Result(0, "gui/501/com.zeus.paper-trading = {\n\tstate = running\n\tpid = 59087\n}\n")
        return _Result(1, "")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = healthcheck.check()

    assert result["pid"] == 59087
    assert result["daemon_alive"] is True
    assert result["riskguard_alive"] is True
    assert result["healthy"] is True


def test_healthcheck_is_not_healthy_when_daemon_is_dead(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-live.json"
    risk_path = tmp_path / "risk_state-live.db"
    status_path.write_text(json.dumps(_status_payload(
        timestamp=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        portfolio={"open_positions": 0, "total_exposure_usd": 0.0},
    )))
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


@pytest.mark.skip(reason="Phase2: paper mode removed")
def test_healthcheck_is_not_healthy_when_riskguard_is_missing(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-paper.json"
    risk_path = tmp_path / "risk_state-paper.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
    )))
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


def test_healthcheck_is_not_healthy_when_last_cycle_failed(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-paper.json"
    risk_path = tmp_path / "risk_state-paper.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
        cycle={"failed": True, "failure_reason": "boom"},
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "paper")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.paper-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["cycle_failed"] is True
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_flags_stale_status_and_risk_contracts(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-paper.json"
    risk_path = tmp_path / "risk_state-paper.db"
    status_path.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "risk": {"level": "GREEN", "details": {}},
                "portfolio": {"open_positions": 1, "total_exposure_usd": 6.99},
                "cycle": {},
            }
        )
    )
    _write_risk_state(
        risk_path,
        details={"brier_level": "GREEN"},
    )

    monkeypatch.setenv("ZEUS_MODE", "paper")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.paper-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["status_contract_valid"] is False
    assert "control" in result["status_contract_missing_keys"]
    assert result["riskguard_contract_valid"] is False
    assert "execution_quality_level" in result["riskguard_contract_missing_keys"]
    assert result["recommended_commands"] == []
    assert result["healthy"] is False
