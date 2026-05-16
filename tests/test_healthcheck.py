# Created: 2026-04-30
# Last reused/audited: 2026-05-16
# Authority basis: first-principles ZEUS_MODE cleanup 2026-04-30; healthcheck live-only runtime contract; docs/operations/task_2026-05-16_live_continuous_run_package/LIVE_CONTINUOUS_RUN_PACKAGE_PLAN.md Phase C.
from __future__ import annotations
import pytest

import json
import plistlib
import sqlite3
from datetime import datetime, timedelta, timezone

from scripts import healthcheck

_ORIGINAL_LAUNCHD_CONTRACTS = healthcheck._launchd_contracts
_ORIGINAL_SOURCE_HEALTH_STATUS = healthcheck._source_health_status
_ORIGINAL_LIVE_DB_HOLDER_STATUS = healthcheck._live_db_holder_status


@pytest.fixture(autouse=True)
def _mock_run_validation(monkeypatch):
    """Healthcheck calls validate_assumptions.run_validation which reads
    state/assumptions.json from disk. Mock to keep tests hermetic."""
    monkeypatch.setattr(
        "scripts.validate_assumptions.run_validation",
        lambda: {"valid": True, "mismatches": []},
    )


@pytest.fixture(autouse=True)
def _mock_code_plane_identity(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_code_plane_identity",
        lambda: {
            "status": "ok",
            "repo": "/tmp/zeus",
            "head": "expected-commit",
            "branch": "main",
            "dirty": False,
            "expected_ref": "origin/main",
            "expected_commit": "expected-commit",
            "expected_error": None,
            "matches_expected": True,
        },
    )


@pytest.fixture(autouse=True)
def _mock_launchd_contracts(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_launchd_contracts",
        lambda: {"ok": True, "launchagents_dir": "/tmp/LaunchAgents", "items": []},
    )


@pytest.fixture(autouse=True)
def _mock_source_health_status(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_source_health_status",
        lambda: {
            "ok": True,
            "path": "/tmp/source_health.json",
            "branch": "FRESH",
            "issue": None,
            "written_at_age_seconds": 1.0,
            "writer_fresh": True,
            "stale_sources": [],
        },
    )


@pytest.fixture(autouse=True)
def _mock_live_db_holder_status(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_live_db_holder_status",
        lambda: {
            "ok": True,
            "path": "/tmp/zeus_trades.db",
            "holders": [],
            "unknown_long_lived_holders": [],
        },
    )


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


def _status_payload(*, timestamp=None, risk=None, portfolio=None, cycle=None, execution=None, strategy=None, learning=None, control=None, runtime=None, execution_capability=None):
    payload = {
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
    if execution_capability is not None:
        payload["execution_capability"] = execution_capability
    return payload


def _write_no_trade_artifact(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS decision_log (id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, artifact_json TEXT NOT NULL, timestamp TEXT NOT NULL, env TEXT NOT NULL DEFAULT 'live')"
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
        ("opening_hunt", "2026-04-02T00:00:00Z", "2026-04-02T00:01:00Z", json.dumps(artifact), datetime.now(timezone.utc).isoformat(), "live"),
    )
    conn.commit()
    conn.close()


def _write_source_health(path, *, written_at=None, stale_source=None):
    now = datetime.now(timezone.utc)
    payload = {
        "written_at": written_at or now.isoformat(),
        "sources": {},
    }
    source_budgets = {
        "open_meteo_archive": 6 * 3600,
        "wu_pws": 6 * 3600,
        "hko": 36 * 3600,
        "ogimet": 36 * 3600,
        "ecmwf_open_data": 24 * 3600,
        "noaa": 36 * 3600,
        "tigge_mars": 24 * 3600,
    }
    for source, budget_seconds in source_budgets.items():
        age_seconds = budget_seconds // 2
        if source == stale_source:
            age_seconds = budget_seconds + 60
        last_success = (now - timedelta(seconds=age_seconds)).isoformat()
        payload["sources"][source] = {
            "last_success_at": last_success,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "degraded_since": None,
            "latency_ms": 100,
            "error": None,
        }
    path.write_text(json.dumps(payload))
    return path


def _write_control_plane(path, *, force_ignore_freshness):
    path.write_text(json.dumps({"force_ignore_freshness": force_ignore_freshness}))
    return path


def _execution_capability_with_collateral_db_lock():
    component = {
        "component": "collateral_ledger_global",
        "allowed": False,
        "reason": "summary_unavailable",
        "details": {
            "loader_failed": True,
            "error_type": "OperationalError",
            "error": "database is locked",
            "authority_tier": "DEGRADED",
        },
    }
    return {
        "entry": {"components": [component]},
        "exit": {"components": [component]},
    }


def _write_launchd_plist(
    launchagents_dir,
    *,
    label,
    module,
    root,
    keep_alive=True,
    run_at_load=True,
    throttle_interval=30,
    working_directory=None,
    pythonpath=None,
):
    payload = {
        "Label": label,
        "ProgramArguments": [str(root / ".venv" / "bin" / "python"), "-m", module],
        "WorkingDirectory": str(working_directory or root),
        "RunAtLoad": run_at_load,
        "KeepAlive": keep_alive,
        "ThrottleInterval": throttle_interval,
        "EnvironmentVariables": {
            "PYTHONPATH": str(pythonpath or root),
            "ZEUS_MODE": "live",
        },
    }
    path = launchagents_dir / f"{label}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        plistlib.dump(payload, handle)
    return path


class _LaunchctlResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _launchctl_print_output(
    *,
    label,
    module,
    root,
    plist_path,
    keep_alive=True,
    run_at_load=True,
    minimum_runtime=30,
    state="running",
    pid=1234,
    working_directory=None,
    pythonpath=None,
):
    properties = []
    if keep_alive:
        properties.append("keepalive")
    if run_at_load:
        properties.append("runatload")
    properties.append("inferred program")
    return f"""gui/501/{label} = {{
\tactive count = 1
\tpath = {plist_path}
\ttype = LaunchAgent
\tstate = {state}

\tprogram = {root / ".venv" / "bin" / "python"}
\targuments = {{
\t\t{root / ".venv" / "bin" / "python"}
\t\t-m
\t\t{module}
\t}}

\tworking directory = {working_directory or root}

\tenvironment = {{
\t\tPYTHONPATH => {pythonpath or root}
\t\tXPC_SERVICE_NAME => {label}
\t}}

\tminimum runtime = {minimum_runtime}
\tpid = {pid}
\tproperties = {" | ".join(properties)}
}}
"""


def _mock_launchctl_loaded_contracts(monkeypatch, specs):
    def _run(cmd, *args, **kwargs):
        if cmd[:2] == ["launchctl", "print"]:
            label = cmd[-1].rsplit("/", 1)[-1]
            if label in specs:
                return _LaunchctlResult(0, specs[label])
        return _LaunchctlResult(1, "", "not found")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)


def test_launchd_contracts_require_restart_policy_and_repo_identity(monkeypatch, tmp_path):
    root = tmp_path / "zeus"
    launchagents = tmp_path / "LaunchAgents"
    monkeypatch.setenv("ZEUS_MODE", "live")
    specs = {}
    for label, module in (
        ("com.zeus.live-trading", "src.main"),
        ("com.zeus.riskguard-live", "src.riskguard.riskguard"),
        ("com.zeus.forecast-live", "src.ingest.forecast_live_daemon"),
    ):
        plist_path = _write_launchd_plist(launchagents, label=label, module=module, root=root)
        specs[label] = _launchctl_print_output(
            label=label,
            module=module,
            root=root,
            plist_path=plist_path,
        )
    _mock_launchctl_loaded_contracts(monkeypatch, specs)

    result = _ORIGINAL_LAUNCHD_CONTRACTS(launchagents, root=root)

    assert result["ok"] is True
    assert {item["label"] for item in result["items"]} == {
        "com.zeus.live-trading",
        "com.zeus.riskguard-live",
        "com.zeus.forecast-live",
    }


def test_launchd_contracts_reject_non_restartable_live_trading(monkeypatch, tmp_path):
    root = tmp_path / "zeus"
    launchagents = tmp_path / "LaunchAgents"
    monkeypatch.setenv("ZEUS_MODE", "live")
    live_plist = _write_launchd_plist(
        launchagents,
        label="com.zeus.live-trading",
        module="src.main",
        root=root,
        keep_alive=False,
    )
    _write_launchd_plist(
        launchagents,
        label="com.zeus.riskguard-live",
        module="src.riskguard.riskguard",
        root=root,
    )
    _write_launchd_plist(
        launchagents,
        label="com.zeus.forecast-live",
        module="src.ingest.forecast_live_daemon",
        root=root,
    )
    _mock_launchctl_loaded_contracts(
        monkeypatch,
        {
            "com.zeus.live-trading": _launchctl_print_output(
                label="com.zeus.live-trading",
                module="src.main",
                root=root,
                plist_path=live_plist,
                keep_alive=False,
            ),
            "com.zeus.riskguard-live": _launchctl_print_output(
                label="com.zeus.riskguard-live",
                module="src.riskguard.riskguard",
                root=root,
                plist_path=launchagents / "com.zeus.riskguard-live.plist",
            ),
            "com.zeus.forecast-live": _launchctl_print_output(
                label="com.zeus.forecast-live",
                module="src.ingest.forecast_live_daemon",
                root=root,
                plist_path=launchagents / "com.zeus.forecast-live.plist",
            ),
        },
    )

    result = _ORIGINAL_LAUNCHD_CONTRACTS(launchagents, root=root)

    assert result["ok"] is False
    live_item = next(item for item in result["items"] if item["label"] == "com.zeus.live-trading")
    assert "keepalive_not_true" in live_item["issues"]
    assert "loaded_keepalive_not_true" in live_item["issues"]


def test_launchd_contracts_reject_stale_loaded_contract_after_disk_fix(monkeypatch, tmp_path):
    root = tmp_path / "zeus"
    launchagents = tmp_path / "LaunchAgents"
    monkeypatch.setenv("ZEUS_MODE", "live")
    live_plist = _write_launchd_plist(
        launchagents,
        label="com.zeus.live-trading",
        module="src.main",
        root=root,
        keep_alive=True,
    )
    _write_launchd_plist(
        launchagents,
        label="com.zeus.riskguard-live",
        module="src.riskguard.riskguard",
        root=root,
    )
    _write_launchd_plist(
        launchagents,
        label="com.zeus.forecast-live",
        module="src.ingest.forecast_live_daemon",
        root=root,
    )
    _mock_launchctl_loaded_contracts(
        monkeypatch,
        {
            "com.zeus.live-trading": _launchctl_print_output(
                label="com.zeus.live-trading",
                module="src.main",
                root=root,
                plist_path=live_plist,
                keep_alive=False,
            ),
            "com.zeus.riskguard-live": _launchctl_print_output(
                label="com.zeus.riskguard-live",
                module="src.riskguard.riskguard",
                root=root,
                plist_path=launchagents / "com.zeus.riskguard-live.plist",
            ),
            "com.zeus.forecast-live": _launchctl_print_output(
                label="com.zeus.forecast-live",
                module="src.ingest.forecast_live_daemon",
                root=root,
                plist_path=launchagents / "com.zeus.forecast-live.plist",
            ),
        },
    )

    result = _ORIGINAL_LAUNCHD_CONTRACTS(launchagents, root=root)

    assert result["ok"] is False
    live_item = next(item for item in result["items"] if item["label"] == "com.zeus.live-trading")
    assert "keepalive_not_true" not in live_item["issues"]
    assert "loaded_keepalive_not_true" in live_item["issues"]


def test_source_health_status_requires_writer_and_sources_fresh(monkeypatch, tmp_path):
    source_health_path = _write_source_health(tmp_path / "source_health.json")
    monkeypatch.setattr(healthcheck, "_source_health_path", lambda: source_health_path)

    result = _ORIGINAL_SOURCE_HEALTH_STATUS()

    assert result["ok"] is True
    assert result["branch"] == "FRESH"
    assert result["writer_fresh"] is True
    assert result["stale_sources"] == []


def test_source_health_status_rejects_stale_writer_even_when_sources_fresh(monkeypatch, tmp_path):
    old_written_at = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    source_health_path = _write_source_health(tmp_path / "source_health.json", written_at=old_written_at)
    monkeypatch.setattr(healthcheck, "_source_health_path", lambda: source_health_path)

    result = _ORIGINAL_SOURCE_HEALTH_STATUS()

    assert result["ok"] is False
    assert result["branch"] == "FRESH"
    assert result["writer_fresh"] is False
    assert result["issue"] == "SOURCE_HEALTH_WRITER_STALE"


def test_source_health_status_rejects_stale_required_source(monkeypatch, tmp_path):
    source_health_path = _write_source_health(tmp_path / "source_health.json", stale_source="ecmwf_open_data")
    monkeypatch.setattr(healthcheck, "_source_health_path", lambda: source_health_path)

    result = _ORIGINAL_SOURCE_HEALTH_STATUS()

    assert result["ok"] is False
    assert result["branch"] == "STALE"
    assert result["issue"] == "SOURCE_HEALTH_SOURCE_STALE"
    assert result["stale_sources"] == ["ecmwf_open_data"]


def test_source_health_status_rejects_operator_override_for_live_ready(monkeypatch, tmp_path):
    source_health_path = _write_source_health(tmp_path / "source_health.json", stale_source="ecmwf_open_data")
    _write_control_plane(tmp_path / "control_plane.json", force_ignore_freshness=["ecmwf_open_data"])
    monkeypatch.setattr(healthcheck, "_source_health_path", lambda: source_health_path)

    result = _ORIGINAL_SOURCE_HEALTH_STATUS()

    assert result["ok"] is False
    assert result["branch"] == "FRESH"
    assert result["all_sources_fresh"] is False
    assert result["operator_overrides"] == ["ecmwf_open_data"]
    assert result["issue"] == "SOURCE_HEALTH_OPERATOR_OVERRIDE"


def test_live_db_holder_status_blocks_unknown_long_lived_holder(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    db_path.write_text("")
    (tmp_path / "zeus_trades.db-wal").write_text("")
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    class _Result:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _run(cmd, *args, **kwargs):
        if cmd[0] == "lsof":
            return _Result(0, f"p4242\nn{db_path}\nn{db_path}-wal\n")
        if cmd[:3] == ["ps", "-p", "4242"]:
            return _Result(0, "12:01 python gyoshu_bridge.py --live-db\n")
        return _Result(1, "", "unexpected command")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "LIVE_DB_UNKNOWN_LONG_LIVED_HOLDER"
    assert result["unknown_long_lived_holders"][0]["pid"] == 4242


def test_live_db_holder_status_blocks_long_lived_pytest_holder(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    db_path.write_text("")
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    class _Result:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _run(cmd, *args, **kwargs):
        if cmd[0] == "lsof":
            return _Result(0, f"p5151\nn{db_path}\n")
        if cmd[:3] == ["ps", "-p", "5151"]:
            return _Result(0, "12:01 pytest tests/test_db.py\n")
        return _Result(1, "", "unexpected command")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "LIVE_DB_UNKNOWN_LONG_LIVED_HOLDER"
    assert result["unknown_long_lived_holders"][0]["pid"] == 5151


def test_live_db_holder_status_allows_known_live_owner(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    db_path.write_text("")
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    class _Result:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _run(cmd, *args, **kwargs):
        if cmd[0] == "lsof":
            return _Result(0, f"p111\nn{db_path}\n")
        if cmd[:3] == ["ps", "-p", "111"]:
            return _Result(0, "2-00:00:00 /tmp/zeus/.venv/bin/python -m src.main\n")
        return _Result(1, "", "unexpected command")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is True
    assert result["holders"][0]["known_live_owner"] is True
    assert result["unknown_long_lived_holders"] == []


def test_live_db_holder_status_lsof_unavailable_blocks_ready(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    db_path.write_text("")
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    def _run(cmd, *args, **kwargs):
        raise FileNotFoundError("lsof")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is False
    assert result["diagnostic_unavailable"] is True
    assert result["issue"] == "LIVE_DB_HOLDER_ATTESTATION_UNAVAILABLE"


def test_live_db_holder_status_lsof_permission_error_blocks_ready(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    db_path.write_text("")
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "lsof: permission denied"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is False
    assert result["diagnostic_unavailable"] is True
    assert result["issue"] == "LIVE_DB_HOLDER_ATTESTATION_FAILED"


def test_live_db_holder_status_partial_lsof_with_stderr_blocks_ready(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    db_path.write_text("")
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    class _Result:
        returncode = 1
        stdout = f"p4242\nn{db_path}\n"
        stderr = "lsof: permission denied"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is False
    assert result["diagnostic_unavailable"] is True
    assert result["issue"] == "LIVE_DB_HOLDER_ATTESTATION_FAILED"


def test_live_db_holder_status_blocks_unknown_holder_when_ps_unavailable(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    db_path.write_text("")
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    class _Result:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _run(cmd, *args, **kwargs):
        if cmd[0] == "lsof":
            return _Result(0, f"p4242\nn{db_path}\n")
        if cmd[:3] == ["ps", "-p", "4242"]:
            return _Result(1, "", "ps failed")
        return _Result(1, "", "unexpected command")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "LIVE_DB_HOLDER_ATTESTATION_INCOMPLETE"
    assert result["unattested_holders"][0]["pid"] == 4242


def test_live_db_holder_status_allows_unknown_short_lived_holder(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    db_path.write_text("")
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    class _Result:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _run(cmd, *args, **kwargs):
        if cmd[0] == "lsof":
            return _Result(0, f"p4242\nn{db_path}\n")
        if cmd[:3] == ["ps", "-p", "4242"]:
            return _Result(0, "00:12 python scripts/check_data_pipeline_live_e2e.py\n")
        return _Result(1, "", "unexpected command")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is True
    assert result["unknown_long_lived_holders"] == []
    assert result["unattested_holders"] == []


def test_healthcheck_uses_mode_qualified_status_and_reports_healthy(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-live.json"
    risk_path = tmp_path / "risk_state-live.db"
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

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["mode"] == "live"
    assert result["daemon_alive"] is True
    assert result["riskguard_alive"] is True
    assert result["status_path"] == str(status_path)
    assert result["status_fresh"] is True
    assert result["status_contract_valid"] is True
    assert result["riskguard_fresh"] is True
    assert result["riskguard_contract_valid"] is True
    assert result["code_plane_ok"] is True
    assert result["launchd_contract_ok"] is True
    assert result["source_health_ok"] is True
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


def test_healthcheck_is_not_healthy_when_code_plane_drifts(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload()))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)
    monkeypatch.setattr(
        healthcheck,
        "_code_plane_identity",
        lambda: {
            "status": "ok",
            "repo": "/tmp/zeus",
            "head": "running-commit",
            "branch": "deploy/live",
            "dirty": True,
            "expected_ref": "origin/main",
            "expected_commit": "main-commit",
            "expected_error": None,
            "matches_expected": False,
        },
    )

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["code_plane_ok"] is False
    assert result["code_plane_issue"] == "LIVE_CODE_PLANE_DRIFT"
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_is_not_healthy_when_launchd_contract_drifts(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload()))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)
    monkeypatch.setattr(
        healthcheck,
        "_launchd_contracts",
        lambda: {
            "ok": False,
            "launchagents_dir": "/tmp/LaunchAgents",
            "items": [
                {
                    "label": "com.zeus.live-trading",
                    "ok": False,
                    "issues": ["keepalive_not_true"],
                }
            ],
        },
    )

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["launchd_contract_ok"] is False
    assert result["launchd_contract_issue"] == "LIVE_LAUNCHD_CONTRACT_DRIFT"
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_is_not_healthy_when_source_health_is_stale(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload()))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)
    monkeypatch.setattr(
        healthcheck,
        "_source_health_status",
        lambda: {
            "ok": False,
            "path": str(tmp_path / "source_health.json"),
            "branch": "FRESH",
            "issue": "SOURCE_HEALTH_WRITER_STALE",
            "writer_fresh": False,
            "stale_sources": [],
        },
    )

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["source_health_ok"] is False
    assert result["source_health_issue"] == "SOURCE_HEALTH_WRITER_STALE"
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_is_not_healthy_when_execution_capability_reports_db_lock(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        execution_capability=_execution_capability_with_collateral_db_lock(),
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["db_lock_ok"] is False
    assert result["db_lock_issue"] == "LIVE_DB_LOCK_EXECUTION_CAPABILITY"
    assert result["db_lock_status"]["locks"][0]["component"] == "collateral_ledger_global"
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_is_not_healthy_when_unknown_long_lived_db_holder_exists(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload()))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)
    monkeypatch.setattr(
        healthcheck,
        "_live_db_holder_status",
        lambda: {
            "ok": False,
            "path": str(tmp_path / "zeus_trades.db"),
            "holders": [],
            "unknown_long_lived_holders": [{"pid": 4242, "command": "python gyoshu_bridge.py"}],
            "issue": "LIVE_DB_UNKNOWN_LONG_LIVED_HOLDER",
        },
    )

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["live_db_holders_ok"] is False
    assert result["live_db_holders_issue"] == "LIVE_DB_UNKNOWN_LONG_LIVED_HOLDER"
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_rejects_stale_loaded_launchd_contract_after_disk_fix(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    root = tmp_path / "zeus"
    launchagents = tmp_path / "LaunchAgents"
    status_path.write_text(json.dumps(_status_payload()))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)
    monkeypatch.setattr(
        healthcheck,
        "_launchd_contracts",
        lambda: _ORIGINAL_LAUNCHD_CONTRACTS(launchagents, root=root),
    )
    live_plist = _write_launchd_plist(
        launchagents,
        label="com.zeus.live-trading",
        module="src.main",
        root=root,
        keep_alive=True,
    )
    _write_launchd_plist(
        launchagents,
        label="com.zeus.riskguard-live",
        module="src.riskguard.riskguard",
        root=root,
    )
    _write_launchd_plist(
        launchagents,
        label="com.zeus.forecast-live",
        module="src.ingest.forecast_live_daemon",
        root=root,
    )
    _mock_launchctl_loaded_contracts(
        monkeypatch,
        {
            "com.zeus.live-trading": _launchctl_print_output(
                label="com.zeus.live-trading",
                module="src.main",
                root=root,
                plist_path=live_plist,
                keep_alive=False,
            ),
            "com.zeus.riskguard-live": _launchctl_print_output(
                label="com.zeus.riskguard-live",
                module="src.riskguard.riskguard",
                root=root,
                plist_path=launchagents / "com.zeus.riskguard-live.plist",
            ),
            "com.zeus.forecast-live": _launchctl_print_output(
                label="com.zeus.forecast-live",
                module="src.ingest.forecast_live_daemon",
                root=root,
                plist_path=launchagents / "com.zeus.forecast-live.plist",
            ),
        },
    )

    result = healthcheck.check()

    assert result["healthy"] is False
    assert result["launchd_contract_issue"] == "LIVE_LAUNCHD_CONTRACT_DRIFT"
    live_item = next(item for item in result["launchd_contracts"]["items"] if item["label"] == "com.zeus.live-trading")
    assert "loaded_keepalive_not_true" in live_item["issues"]
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_parses_launchctl_kv_output(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
    )))
    _write_risk_state(risk_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)

    class _Result:
        returncode = 0
        stdout = '{\n\t"Label" = "com.zeus.live-trading";\n\t"PID" = 59087;\n\t"LastExitStatus" = 15;\n};\n'

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["pid"] == 59087
    assert result["daemon_alive"] is True
    assert result["riskguard_alive"] is True
    assert result["healthy"] is True


def test_healthcheck_defaults_live_riskguard_label(monkeypatch):
    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.delenv("ZEUS_RISKGUARD_LABEL", raising=False)

    assert healthcheck._riskguard_label() == "com.zeus.riskguard-live"


def test_healthcheck_riskguard_label_override_takes_precedence(monkeypatch):
    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setenv("ZEUS_RISKGUARD_LABEL", "com.zeus.riskguard-custom")

    assert healthcheck._riskguard_label() == "com.zeus.riskguard-custom"


def test_healthcheck_falls_back_to_launchctl_print_when_list_fails(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
    )))
    _write_risk_state(risk_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
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
            return _Result(0, "gui/501/com.zeus.live-trading = {\n\tstate = running\n\tpid = 59087\n}\n")
        return _Result(1, "")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = healthcheck.check()

    assert result["pid"] == 59087
    assert result["daemon_alive"] is True
    assert result["riskguard_alive"] is True
    assert result["healthy"] is True


def test_healthcheck_is_not_healthy_when_daemon_is_dead(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
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


def test_healthcheck_is_not_healthy_when_riskguard_is_missing(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary-live.json"
    risk_path = tmp_path / "risk_state-live.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
    )))
    _write_risk_state(risk_path, checked_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)

    def _run(cmd, *args, **kwargs):
        class _Result:
            returncode = 0 if cmd[-1] == "com.zeus.live-trading" else 1
            stdout = "123\t0\tcom.zeus.live-trading\n" if cmd[-1] == "com.zeus.live-trading" else ""
        return _Result()

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = healthcheck.check()

    assert result["daemon_alive"] is True
    assert result["riskguard_alive"] is False
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_is_not_healthy_when_last_cycle_failed(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
        cycle={"failed": True, "failure_reason": "boom"},
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["cycle_failed"] is True
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_projects_force_exit_review_scope(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
        cycle={
            "force_exit_review": True,
            "force_exit_review_scope": "entry_block_only",
            "entries_blocked_reason": "force_exit_review_daily_loss_red",
        },
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["force_exit_review_scope"] == "entry_block_only"
    assert result["entries_blocked_reason"] == "force_exit_review_daily_loss_red"
    assert result["healthy"] is True


def test_healthcheck_projects_yellow_risk_block_reason(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
        cycle={"entries_blocked_reason": "risk_level=YELLOW"},
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["entries_blocked_reason"] == "risk_level=YELLOW"
    assert result["healthy"] is True


def test_healthcheck_projects_infrastructure_red_as_unhealthy(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        risk={
            "level": "GREEN",
            "infrastructure_level": "RED",
            "infrastructure_issues": ["execution_summary_unavailable"],
            "details": {
                "execution_quality_level": "GREEN",
                "strategy_signal_level": "GREEN",
                "recommended_controls": [],
                "recommended_strategy_gates": [],
            },
        },
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["risk_level"] == "GREEN"
    assert result["infrastructure_level"] == "RED"
    assert result["infrastructure_issues"] == ["execution_summary_unavailable"]
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_keeps_infrastructure_yellow_healthy(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        risk={
            "level": "GREEN",
            "infrastructure_level": "YELLOW",
            "infrastructure_issues": ["cycle_risk_level_mismatch:YELLOW->GREEN"],
            "details": {
                "execution_quality_level": "GREEN",
                "strategy_signal_level": "GREEN",
                "recommended_controls": [],
                "recommended_strategy_gates": [],
            },
        },
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["infrastructure_level"] == "YELLOW"
    assert result["infrastructure_issues"] == ["cycle_risk_level_mismatch:YELLOW->GREEN"]
    assert result["healthy"] is True
    assert healthcheck.exit_code_for(result) == 0


def test_healthcheck_projects_quarantine_expired_cycle_field(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
        cycle={
            "entries_blocked_reason": "portfolio_quarantined",
            "portfolio_quarantined": True,
            "quarantine_expired": 2,
        },
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["entries_blocked_reason"] == "portfolio_quarantined"
    assert result["quarantine_expired"] == 2
    assert result["healthy"] is True


def test_healthcheck_projects_current_auto_pause_reason_from_control(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        portfolio={"open_positions": 1, "total_exposure_usd": 6.99},
        cycle={
            "entries_paused": True,
            "entries_pause_reason": "auto_pause:StaleCycleReason",
        },
        control={"entries_paused": True, "entries_pause_source": "auto_exception", "entries_pause_reason": "auto_pause:ValueError", **{
            "strategy_gates": {},
            "recommended_but_not_gated": [],
            "gated_but_not_recommended": [],
            "recommended_controls_not_applied": [],
            "recommended_auto_commands": [],
            "review_required_commands": [],
            "recommended_commands": [],
        }},
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["entries_pause_source"] == "auto_exception"
    assert result["entries_pause_reason"] == "auto_pause:ValueError"
    assert result["control_state"]["entries_paused"] is True
    assert result["healthy"] is True


def test_healthcheck_flags_stale_status_and_risk_contracts(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
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

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["status_contract_valid"] is False
    assert "control" in result["status_contract_missing_keys"]
    assert result["riskguard_contract_valid"] is False
    assert "execution_quality_level" in result["riskguard_contract_missing_keys"]
    assert result["recommended_commands"] == []
    assert result["healthy"] is False


def test_phase_c4_flag_off_healthy_unaffected_by_entry_forecast_blockers(monkeypatch, tmp_path):
    """Phase C-4: with ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS unset
    (default OFF), a populated ``entry_forecast_blockers`` list does
    NOT flip ``result["healthy"]`` to False. This preserves the legacy
    "GREEN even if entry-forecast is BLOCKED" behavior so daemons in
    flag-default state see no observability change.
    """

    status_path = tmp_path / "status_summary-live.json"
    risk_path = tmp_path / "risk_state-live.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        risk={"level": "GREEN", "details": {
            "execution_quality_level": "GREEN",
            "strategy_signal_level": "GREEN",
            "recommended_controls": [],
            "recommended_strategy_gates": [],
        }},
        portfolio={"open_positions": 0, "total_exposure_usd": 0.0},
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.delenv("ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS", raising=False)
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)
    # World DB intentionally absent → entry_forecast_blockers populated
    monkeypatch.setattr(healthcheck, "_world_db_path", lambda: tmp_path / "absent_world.db")

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["entry_forecast_blockers"] == ["ENTRY_FORECAST_WORLD_DB_MISSING"]
    assert result["healthy"] is True


def test_phase_c4_flag_on_healthy_false_when_entry_forecast_blocked(monkeypatch, tmp_path):
    """Phase C-4: with the flag ON, a populated
    ``entry_forecast_blockers`` list pulls ``result["healthy"]`` False
    even when every other sub-check is green. This closes the
    fail-OPEN seam critic-opus ATTACK 4 surfaced (healthcheck used to
    stay GREEN even when the live entry-forecast layer was BLOCKED).
    """

    status_path = tmp_path / "status_summary-live.json"
    risk_path = tmp_path / "risk_state-live.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        risk={"level": "GREEN", "details": {
            "execution_quality_level": "GREEN",
            "strategy_signal_level": "GREEN",
            "recommended_controls": [],
            "recommended_strategy_gates": [],
        }},
        portfolio={"open_positions": 0, "total_exposure_usd": 0.0},
    )))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS", "1")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)
    monkeypatch.setattr(healthcheck, "_world_db_path", lambda: tmp_path / "absent_world.db")

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["entry_forecast_blockers"] == ["ENTRY_FORECAST_WORLD_DB_MISSING"]
    assert result["healthy"] is False
