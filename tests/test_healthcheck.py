# Lifecycle: created=2026-04-30; last_reviewed=2026-07-08; last_reused=2026-07-08
# Purpose: Lock healthcheck relationship predicates for live daemon, launchd, entry capability, and settlement truth.
# Reuse: Run when scripts/healthcheck.py health predicates or live readiness status fields change.
# Created: 2026-04-30
# Last reused/audited: 2026-07-08
# Authority basis: first-principles ZEUS_MODE cleanup 2026-04-30; healthcheck live-only runtime contract; docs/archive/2026-Q2/task_2026-05-16_live_continuous_run_package/LIVE_CONTINUOUS_RUN_PACKAGE_PLAN.md Phase C; 2026-05-17 riskguard live DB-holder health contract.
from __future__ import annotations
import pytest

import json
import plistlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import healthcheck

_ORIGINAL_LAUNCHD_CONTRACTS = healthcheck._launchd_contracts
_ORIGINAL_SOURCE_HEALTH_STATUS = healthcheck._source_health_status
_ORIGINAL_LIVE_DB_HOLDER_STATUS = healthcheck._live_db_holder_status
_ORIGINAL_POSITION_CURRENT_SCHEMA_STATUS = healthcheck._position_current_schema_status
_ORIGINAL_MONITOR_PROBABILITY_FRESHNESS_STATUS = (
    healthcheck._monitor_probability_freshness_status
)
_ORIGINAL_MAIN_DAEMON_ATTESTATION_SURFACE = (
    healthcheck._main_daemon_attestation_surface
)
_ORIGINAL_VENUE_COMMANDS_SCHEMA_STATUS = healthcheck._venue_commands_schema_status
_ORIGINAL_TERMINAL_ENTRY_COMMAND_VENUE_FACT_CONFLICTS_STATUS = (
    healthcheck._terminal_entry_command_venue_fact_conflicts_status
)
_ORIGINAL_MONITOR_CADENCE_STATUS = healthcheck._monitor_cadence_status
_ORIGINAL_EDLI_QUEUE_STATUS = healthcheck._edli_queue_status
_ORIGINAL_FORECAST_POSTERIORS_SCHEMA_STATUS = (
    healthcheck._forecast_posteriors_runtime_layer_schema_status
)
_ORIGINAL_PROCESS_LOADED_CODE_STATUS = healthcheck._process_loaded_code_status
_ORIGINAL_SETTLEMENT_TRUTH_STATUS = healthcheck._settlement_truth_status

_LAUNCHD_TEST_SPECS = (
    ("com.zeus.live-trading", "src.main"),
    ("com.zeus.data-ingest", "src.ingest_main"),
    ("com.zeus.riskguard-live", "src.riskguard.riskguard"),
    ("com.zeus.forecast-live", "src.ingest.forecast_live_daemon"),
    ("com.zeus.venue-heartbeat", "src.control.heartbeat_supervisor"),
)


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


@pytest.fixture(autouse=True)
def _mock_position_current_schema_status(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_position_current_schema_status",
        lambda: {"ok": True, "path": "/tmp/zeus_trades.db", "missing_columns": []},
    )


@pytest.fixture(autouse=True)
def _mock_venue_commands_schema_status(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_venue_commands_schema_status",
        lambda: {"ok": True, "path": "/tmp/zeus_trades.db", "missing_columns": []},
    )


@pytest.fixture(autouse=True)
def _mock_terminal_entry_command_venue_fact_conflicts_status(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_terminal_entry_command_venue_fact_conflicts_status",
        lambda: {
            "ok": True,
            "path": "/tmp/zeus_trades.db",
            "count": 0,
            "sample": [],
        },
    )


@pytest.fixture(autouse=True)
def _mock_monitor_cadence_status(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_monitor_cadence_status",
        lambda: {"ok": True, "path": "/tmp/zeus_trades.db", "issue": None},
    )


@pytest.fixture(autouse=True)
def _mock_edli_queue_status(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_edli_queue_status",
        lambda: {
            "ok": True,
            "path": "/tmp/zeus-world.db",
            "issue": None,
            "consumer_name": "edli_reactor_v1",
        },
    )


@pytest.fixture(autouse=True)
def _mock_forecast_posteriors_schema_status(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_forecast_posteriors_runtime_layer_schema_status",
        lambda: {
            "ok": True,
            "path": "/tmp/zeus-forecasts.db",
            "issue": None,
            "runtime_layer_supported": True,
        },
    )


@pytest.fixture(autouse=True)
def _mock_process_loaded_code_status(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_process_loaded_code_status",
        lambda launchd_contracts: {"ok": True, "issue": None, "stale": [], "unattested": [], "items": []},
    )


@pytest.fixture(autouse=True)
def _mock_optional_live_projection_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(
        healthcheck,
        "_live_health_composite_path",
        lambda: tmp_path / "missing-live-health-composite.json",
    )
    monkeypatch.setattr(
        healthcheck,
        "_venue_heartbeat_keeper_path",
        lambda: tmp_path / "missing-venue-heartbeat-keeper.json",
    )


@pytest.fixture(autouse=True)
def _mock_settlement_truth_status(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_settlement_truth_status",
        lambda: {
            "ok": True,
            "path": "/tmp/zeus-forecasts.db",
            "count": 1,
            "max_settled_at": datetime.now(timezone.utc).isoformat(),
            "age_seconds": 1.0,
            "issue": None,
        },
    )
    # run_validation reads state/assumptions.json which doesn't exist in CI/test
    # environments. Patch it so assumption mismatch never blocks healthy predicate
    # in tests that exercise check() holistically.
    import scripts.validate_assumptions as _va
    monkeypatch.setattr(
        _va,
        "run_validation",
        lambda: {"valid": True, "checks": ["mocked"], "mismatches": []},
    )


@pytest.fixture(autouse=True)
def _mock_scheduler_business_liveness_status(monkeypatch):
    # _scheduler_business_liveness_status reads scheduler_health.json from disk.
    # Missing file → ok=False → healthy=False for all tests calling check().
    # Mock to "ok" so the predicate does not gate healthy in the test harness.
    monkeypatch.setattr(
        healthcheck,
        "_scheduler_business_liveness_status",
        lambda: {"ok": True, "modes": {}, "issue": None},
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


def _append_risk_state(path, *, checked_at=None, level="GREEN", details=None):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS risk_state (id INTEGER PRIMARY KEY, level TEXT NOT NULL, details_json TEXT, checked_at TEXT NOT NULL)"
    )
    if details is None:
        details = {
            "execution_quality_level": "GREEN",
            "strategy_signal_level": "GREEN",
            "recommended_controls": [],
            "recommended_strategy_gates": [],
        }
    conn.execute(
        "INSERT INTO risk_state (level, details_json, checked_at) VALUES (?, ?, ?)",
        (level, json.dumps(details), checked_at or datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def _status_payload(*, timestamp=None, risk=None, portfolio=None, cycle=None, execution=None, strategy=None, learning=None, control=None, runtime=None, execution_capability=None, process=None):
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
        "process": process or {},
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
    env_extra=None,
):
    env = {
        "PYTHONPATH": str(pythonpath or root),
        "ZEUS_MODE": "live",
    }
    if env_extra:
        env.update(env_extra)
    payload = {
        "Label": label,
        "ProgramArguments": [str(root / ".venv" / "bin" / "python"), "-m", module],
        "WorkingDirectory": str(working_directory or root),
        "RunAtLoad": run_at_load,
        "KeepAlive": keep_alive,
        "ThrottleInterval": throttle_interval,
        "EnvironmentVariables": env,
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
    last_exit_code="",
    env_extra=None,
):
    env_lines = [
        f"\t\tPYTHONPATH => {pythonpath or root}",
        f"\t\tXPC_SERVICE_NAME => {label}",
    ]
    if env_extra:
        env_lines.extend(f"\t\t{key} => {value}" for key, value in env_extra.items())
    env_text = "\n".join(env_lines)
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
{env_text}
\t}}

\tminimum runtime = {minimum_runtime}
\tpid = {pid}
\tlast exit code = {last_exit_code}
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
    for label, module in _LAUNCHD_TEST_SPECS:
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
        "com.zeus.data-ingest",
        "com.zeus.riskguard-live",
        "com.zeus.forecast-live",
        "com.zeus.venue-heartbeat",
    }


def test_launchd_contracts_reject_venue_heartbeat_timeout_that_consumes_cadence(
    monkeypatch,
    tmp_path,
):
    root = tmp_path / "zeus"
    launchagents = tmp_path / "LaunchAgents"
    monkeypatch.setenv("ZEUS_MODE", "live")
    specs = {}
    for label, module in _LAUNCHD_TEST_SPECS:
        env_extra = (
            {
                "ZEUS_HEARTBEAT_CADENCE_SECONDS": "5",
                "ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS": "4",
            }
            if label == "com.zeus.venue-heartbeat"
            else None
        )
        plist_path = _write_launchd_plist(
            launchagents,
            label=label,
            module=module,
            root=root,
            env_extra=env_extra,
        )
        specs[label] = _launchctl_print_output(
            label=label,
            module=module,
            root=root,
            plist_path=plist_path,
            env_extra=env_extra,
        )
    _mock_launchctl_loaded_contracts(monkeypatch, specs)

    result = _ORIGINAL_LAUNCHD_CONTRACTS(launchagents, root=root)

    assert result["ok"] is False
    venue = next(item for item in result["items"] if item["label"] == "com.zeus.venue-heartbeat")
    assert "heartbeat_http_timeout_exceeds_half_cadence" in venue["issues"]
    assert "loaded_heartbeat_http_timeout_exceeds_half_cadence" in venue["issues"]
    assert venue["heartbeat_timing"] == {"cadence_seconds": 5, "http_timeout_seconds": 4.0}
    assert venue["loaded"]["heartbeat_timing"] == {
        "cadence_seconds": 5,
        "http_timeout_seconds": 4.0,
    }


def test_launchd_contracts_reject_stale_loaded_venue_heartbeat_timeout(
    monkeypatch,
    tmp_path,
):
    root = tmp_path / "zeus"
    launchagents = tmp_path / "LaunchAgents"
    monkeypatch.setenv("ZEUS_MODE", "live")
    specs = {}
    for label, module in _LAUNCHD_TEST_SPECS:
        disk_env = (
            {
                "ZEUS_HEARTBEAT_CADENCE_SECONDS": "5",
                "ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS": "1",
            }
            if label == "com.zeus.venue-heartbeat"
            else None
        )
        loaded_env = (
            {
                "ZEUS_HEARTBEAT_CADENCE_SECONDS": "5",
                "ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS": "4",
            }
            if label == "com.zeus.venue-heartbeat"
            else disk_env
        )
        plist_path = _write_launchd_plist(
            launchagents,
            label=label,
            module=module,
            root=root,
            env_extra=disk_env,
        )
        specs[label] = _launchctl_print_output(
            label=label,
            module=module,
            root=root,
            plist_path=plist_path,
            env_extra=loaded_env,
        )
    _mock_launchctl_loaded_contracts(monkeypatch, specs)

    result = _ORIGINAL_LAUNCHD_CONTRACTS(launchagents, root=root)

    assert result["ok"] is False
    venue = next(item for item in result["items"] if item["label"] == "com.zeus.venue-heartbeat")
    assert "heartbeat_http_timeout_exceeds_half_cadence" not in venue["issues"]
    assert "loaded_heartbeat_http_timeout_exceeds_half_cadence" in venue["issues"]


def test_launchd_contracts_treat_running_forecast_prior_exit_as_historical(monkeypatch, tmp_path):
    root = tmp_path / "zeus"
    launchagents = tmp_path / "LaunchAgents"
    monkeypatch.setenv("ZEUS_MODE", "live")
    specs = {}
    for label, module in _LAUNCHD_TEST_SPECS:
        plist_path = _write_launchd_plist(launchagents, label=label, module=module, root=root)
        specs[label] = _launchctl_print_output(
            label=label,
            module=module,
            root=root,
            plist_path=plist_path,
            last_exit_code="1" if label == "com.zeus.forecast-live" else "",
        )
    _mock_launchctl_loaded_contracts(monkeypatch, specs)

    result = _ORIGINAL_LAUNCHD_CONTRACTS(launchagents, root=root)

    assert result["ok"] is True
    forecast_item = next(item for item in result["items"] if item["label"] == "com.zeus.forecast-live")
    assert forecast_item["loaded"]["last_exit_code"] == "1"
    assert "loaded_prior_exit_code_1" not in forecast_item["issues"]
    assert "loaded_prior_exit_code_1" not in forecast_item["loaded"]["issues"]
    assert forecast_item["loaded"]["historical_issues"] == ["loaded_prior_exit_code_1"]


def test_launchd_contracts_reject_prior_exit_when_job_not_running(monkeypatch, tmp_path):
    root = tmp_path / "zeus"
    launchagents = tmp_path / "LaunchAgents"
    monkeypatch.setenv("ZEUS_MODE", "live")
    specs = {}
    for label, module in _LAUNCHD_TEST_SPECS:
        plist_path = _write_launchd_plist(launchagents, label=label, module=module, root=root)
        specs[label] = _launchctl_print_output(
            label=label,
            module=module,
            root=root,
            plist_path=plist_path,
            state="exited" if label == "com.zeus.forecast-live" else "running",
            pid=0 if label == "com.zeus.forecast-live" else 1234,
            last_exit_code="1" if label == "com.zeus.forecast-live" else "",
        )
    _mock_launchctl_loaded_contracts(monkeypatch, specs)

    result = _ORIGINAL_LAUNCHD_CONTRACTS(launchagents, root=root)

    assert result["ok"] is False
    forecast_item = next(item for item in result["items"] if item["label"] == "com.zeus.forecast-live")
    assert "loaded_job_not_running" in forecast_item["issues"]
    assert "loaded_prior_exit_code_1" in forecast_item["issues"]


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
        label="com.zeus.data-ingest",
        module="src.ingest_main",
        root=root,
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
            "com.zeus.data-ingest": _launchctl_print_output(
                label="com.zeus.data-ingest",
                module="src.ingest_main",
                root=root,
                plist_path=launchagents / "com.zeus.data-ingest.plist",
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
        label="com.zeus.data-ingest",
        module="src.ingest_main",
        root=root,
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
            "com.zeus.data-ingest": _launchctl_print_output(
                label="com.zeus.data-ingest",
                module="src.ingest_main",
                root=root,
                plist_path=launchagents / "com.zeus.data-ingest.plist",
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


def test_launchd_contracts_require_data_ingest_support_daemon(monkeypatch, tmp_path):
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

    assert result["ok"] is False
    data_ingest = next(item for item in result["items"] if item["label"] == "com.zeus.data-ingest")
    assert data_ingest["name"] == "data_ingest"
    assert "plist_missing" in data_ingest["issues"]


def test_launchd_contracts_reject_non_dict_plist_without_crashing(tmp_path):
    root = tmp_path / "zeus"
    launchagents = tmp_path / "LaunchAgents"
    launchagents.mkdir()
    path = launchagents / "com.zeus.live-trading.plist"
    with open(path, "wb") as handle:
        plistlib.dump("not-a-dict", handle)

    result = _ORIGINAL_LAUNCHD_CONTRACTS(launchagents, root=root)

    assert result["ok"] is False
    live_item = next(item for item in result["items"] if item["label"] == "com.zeus.live-trading")
    assert "plist_not_dict" in live_item["issues"]


def test_launchd_contracts_reject_non_dict_environment_without_crashing(monkeypatch, tmp_path):
    root = tmp_path / "zeus"
    launchagents = tmp_path / "LaunchAgents"
    payload = {
        "Label": "com.zeus.live-trading",
        "ProgramArguments": [str(root / ".venv" / "bin" / "python"), "-m", "src.main"],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "EnvironmentVariables": ["PYTHONPATH", str(root)],
    }
    launchagents.mkdir()
    path = launchagents / "com.zeus.live-trading.plist"
    with open(path, "wb") as handle:
        plistlib.dump(payload, handle)
    _mock_launchctl_loaded_contracts(
        monkeypatch,
        {
            "com.zeus.live-trading": _launchctl_print_output(
                label="com.zeus.live-trading",
                module="src.main",
                root=root,
                plist_path=path,
            ),
        },
    )

    result = _ORIGINAL_LAUNCHD_CONTRACTS(launchagents, root=root)

    assert result["ok"] is False
    live_item = next(item for item in result["items"] if item["label"] == "com.zeus.live-trading")
    assert "environment_variables_not_dict" in live_item["issues"]
    assert "pythonpath_mismatch" in live_item["issues"]


def test_launchd_contracts_reject_live_trading_shadow_env(monkeypatch, tmp_path):
    root = tmp_path / "zeus"
    launchagents = tmp_path / "LaunchAgents"
    specs = {}
    for label, module in _LAUNCHD_TEST_SPECS:
        env_extra = (
            {"ZEUS_OPPORTUNITY_BOOK_SHADOW": "1"}
            if label == "com.zeus.live-trading"
            else None
        )
        plist_path = _write_launchd_plist(
            launchagents,
            label=label,
            module=module,
            root=root,
            env_extra=env_extra,
        )
        specs[label] = _launchctl_print_output(
            label=label,
            module=module,
            root=root,
            plist_path=plist_path,
            env_extra=env_extra,
        )
    _mock_launchctl_loaded_contracts(monkeypatch, specs)

    result = _ORIGINAL_LAUNCHD_CONTRACTS(launchagents, root=root)

    assert result["ok"] is False
    live_item = next(item for item in result["items"] if item["label"] == "com.zeus.live-trading")
    assert "live_trading_non_submit_env_present:ZEUS_OPPORTUNITY_BOOK_SHADOW" in live_item["issues"]
    assert (
        "loaded_live_trading_non_submit_env_present:ZEUS_OPPORTUNITY_BOOK_SHADOW"
        in live_item["issues"]
    )


def test_source_health_status_requires_writer_and_sources_fresh(monkeypatch, tmp_path):
    source_health_path = _write_source_health(tmp_path / "source_health.json")
    monkeypatch.setattr(healthcheck, "_source_health_path", lambda: source_health_path)

    result = _ORIGINAL_SOURCE_HEALTH_STATUS()

    assert result["ok"] is True
    assert result["branch"] == "FRESH"
    assert result["writer_fresh"] is True
    assert result["stale_sources"] == []


def test_source_health_status_rejects_stale_writer_even_when_sources_fresh(monkeypatch, tmp_path):
    old_written_at = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    source_health_path = _write_source_health(tmp_path / "source_health.json", written_at=old_written_at)
    monkeypatch.setattr(healthcheck, "_source_health_path", lambda: source_health_path)

    result = _ORIGINAL_SOURCE_HEALTH_STATUS()

    assert result["ok"] is False
    assert result["branch"] == "FRESH"
    assert result["writer_fresh"] is False
    assert result["issue"] == "SOURCE_HEALTH_WRITER_STALE"


def test_source_health_status_allows_writer_cadence_jitter(monkeypatch, tmp_path):
    old_written_at = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    source_health_path = _write_source_health(tmp_path / "source_health.json", written_at=old_written_at)
    monkeypatch.setattr(healthcheck, "_source_health_path", lambda: source_health_path)

    result = _ORIGINAL_SOURCE_HEALTH_STATUS()

    assert result["ok"] is True
    assert result["writer_fresh"] is True
    assert result["writer_budget_seconds"] == 15 * 60


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


def test_process_loaded_code_status_rejects_pid_started_before_source_mtime(monkeypatch, tmp_path):
    root = tmp_path / "zeus"
    source = root / "src" / "ingest" / "forecast_live_daemon.py"
    source.parent.mkdir(parents=True)
    source.write_text("# current source\n")
    source_mtime = source.stat().st_mtime
    monkeypatch.setattr(healthcheck, "_process_start_epoch", lambda pid: source_mtime - 60)

    result = _ORIGINAL_PROCESS_LOADED_CODE_STATUS(
        {
            "items": [
                {
                    "name": "forecast_live",
                    "label": "com.zeus.forecast-live",
                    "loaded": {"pid": 202},
                }
            ]
        },
        root=root,
    )

    assert result["ok"] is False
    assert result["issue"] == "PROCESS_LOADED_CODE_STALE"
    assert result["stale"][0]["name"] == "forecast_live"


def test_live_process_loaded_code_surface_includes_recovery_and_m5_paths():
    live_paths = set(healthcheck.PROCESS_CODE_SURFACES["live_trading"])

    assert "src/engine/evaluator.py" in live_paths
    assert "src/control/live_health.py" in live_paths
    assert "src/control/runtime_code_plane.py" in live_paths
    assert "src/engine/cycle_runtime.py" in live_paths
    assert "src/engine/event_reactor_adapter.py" in live_paths
    assert "src/engine/monitor_refresh.py" in live_paths
    assert "src/contracts/executable_market_snapshot.py" in live_paths
    assert "src/contracts/execution_intent.py" in live_paths
    assert "src/data/market_scanner.py" in live_paths
    assert "src/data/polymarket_client.py" in live_paths
    assert "src/control/ws_gap_guard.py" in live_paths
    assert "src/events/reactor.py" in live_paths
    assert "src/execution/command_recovery.py" in live_paths
    assert "src/execution/exchange_reconcile.py" in live_paths
    assert "src/execution/exit_lifecycle.py" in live_paths
    assert "src/execution/staleness_cancel.py" in live_paths
    assert "src/state/chain_mirror_reconciler.py" in live_paths
    assert "src/strategy/selection_family.py" in live_paths
    assert "src/strategy/family_exclusive_dedup.py" in live_paths


def test_settlement_truth_status_rejects_stale_settled_at(tmp_path):
    db_path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE settlement_outcomes (settlement_id INTEGER PRIMARY KEY, settled_at TEXT, recorded_at TEXT)"
    )
    conn.execute(
        "INSERT INTO settlement_outcomes (settled_at, recorded_at) VALUES (?, ?)",
        ("2026-05-11T19:59:13+00:00", "2026-05-11T19:59:13+00:00"),
    )
    conn.commit()
    conn.close()

    result = _ORIGINAL_SETTLEMENT_TRUTH_STATUS(db_path)

    assert result["ok"] is False
    assert result["issue"] == "SETTLEMENT_TRUTH_STALE"
    assert result["count"] == 1


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
            return _Result(0, "12:01 python /tmp/gyoshu_bridge.py --token secret-token --live-db\n")
        return _Result(1, "", "unexpected command")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "LIVE_DB_UNKNOWN_LONG_LIVED_HOLDER"
    assert result["unknown_long_lived_holders"][0]["pid"] == 4242
    assert result["unknown_long_lived_holders"][0]["command"] == "gyoshu_bridge.py"
    assert "secret-token" not in json.dumps(result)


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


def test_live_db_holder_status_allows_riskguard_live_owner(monkeypatch, tmp_path):
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
            return _Result(0, f"p333\nn{db_path}\n")
        if cmd[:3] == ["ps", "-p", "333"]:
            return _Result(
                0,
                "2-00:00:00 /tmp/zeus/.venv/bin/python -m src.riskguard.riskguard\n",
            )
        return _Result(1, "", "unexpected command")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is True
    assert result["holders"][0]["known_live_owner"] is True
    assert result["unknown_long_lived_holders"] == []


@pytest.mark.parametrize(
    "module",
    [
        "src.ingest.substrate_observer_daemon",
        "src.ingest.price_channel_daemon",
        "src.ingest.post_trade_capital_daemon",
    ],
)
def test_live_db_holder_status_allows_declared_split_daemons(monkeypatch, tmp_path, module):
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
            return _Result(0, f"p444\nn{db_path}\n")
        if cmd[:3] == ["ps", "-p", "444"]:
            return _Result(0, f"2-00:00:00 /tmp/zeus/.venv/bin/python -m {module}\n")
        return _Result(1, "", "unexpected command")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is True
    assert result["holders"][0]["known_live_owner"] is True
    assert result["unknown_long_lived_holders"] == []


def test_position_current_schema_status_rejects_missing_monitor_freshness_cols(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_POSITION_CURRENT_SCHEMA_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "POSITION_CURRENT_MONITOR_FRESHNESS_SCHEMA_DRIFT"
    assert result["missing_columns"] == [
        "last_monitor_market_price_is_fresh",
        "last_monitor_prob_is_fresh",
    ]


def test_position_current_schema_status_accepts_monitor_freshness_cols(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            last_monitor_prob_is_fresh INTEGER,
            last_monitor_market_price_is_fresh INTEGER
        )
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_POSITION_CURRENT_SCHEMA_STATUS()

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["missing_columns"] == []


def _write_monitor_freshness_position_db(
    db_path: Path,
    *,
    is_fresh: int,
    phase: str = "active",
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            order_status TEXT,
            shares REAL,
            chain_shares REAL,
            last_monitor_prob REAL,
            last_monitor_prob_is_fresh INTEGER,
            updated_at TEXT,
            city TEXT,
            target_date TEXT,
            bin_label TEXT,
            direction TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, order_status, shares, chain_shares,
            last_monitor_prob, last_monitor_prob_is_fresh, updated_at,
            city, target_date, bin_label, direction
        ) VALUES (
            'pos-monitor', ?, 'filled', 10.0, 10.0,
            0.81, ?, '2026-07-08T20:25:00+00:00',
            'Seoul', '2026-07-10',
            'Will the highest temperature in Seoul be 28°C on July 10?',
            'buy_no'
        )
        """,
        (phase, is_fresh),
    )
    conn.commit()
    conn.close()


def test_monitor_probability_freshness_status_rejects_active_stale_projection(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    _write_monitor_freshness_position_db(db_path, is_fresh=0)
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)
    monkeypatch.setattr(
        healthcheck,
        "_main_daemon_attestation_surface",
        lambda: {"ok": True, "issue": None, "attested": True, "pid": 1234},
    )

    result = _ORIGINAL_MONITOR_PROBABILITY_FRESHNESS_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "MONITOR_PROBABILITY_STALE_CURRENT:n=1"
    assert result["current_stale_projection_count"] == 1
    assert result["current_stale_projection_sample"][0]["position_id"] == "pos-monitor"


def test_monitor_probability_freshness_status_accepts_active_fresh_projection(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    _write_monitor_freshness_position_db(db_path, is_fresh=1)
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)
    monkeypatch.setattr(
        healthcheck,
        "_main_daemon_attestation_surface",
        lambda: {"ok": True, "issue": None, "attested": True, "pid": 1234},
    )

    result = _ORIGINAL_MONITOR_PROBABILITY_FRESHNESS_STATUS()

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["current_stale_projection_count"] == 0


def test_monitor_probability_freshness_status_evaluates_without_daemon_attestation(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    _write_monitor_freshness_position_db(db_path, is_fresh=0)
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)
    monkeypatch.setattr(
        healthcheck,
        "_main_daemon_attestation_surface",
        lambda: {"ok": True, "issue": None, "attested": False, "pid": None},
    )

    result = _ORIGINAL_MONITOR_PROBABILITY_FRESHNESS_STATUS()

    assert result["ok"] is False
    assert result["evaluated"] is True
    assert result["issue"] == "MONITOR_PROBABILITY_STALE_CURRENT:n=1"
    assert result["main_daemon_attested"] is False


def test_venue_commands_schema_status_rejects_missing_q_version(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            state TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_VENUE_COMMANDS_SCHEMA_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "VENUE_COMMANDS_SUBMIT_SCHEMA_DRIFT"
    assert result["missing_columns"] == ["q_version"]


def test_venue_commands_schema_status_accepts_q_version(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            q_version TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_VENUE_COMMANDS_SCHEMA_STATUS()

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["missing_columns"] == []


def test_venue_commands_schema_status_rejects_active_entry_missing_q_version(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            intent_kind TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            q_version TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            shares REAL,
            chain_shares REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, intent_kind, state, created_at, q_version
        ) VALUES (
            'cmd-no-q', 'pos-no-q', 'ENTRY', 'FILLED',
            '2026-07-08T18:26:41+00:00', ''
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, chain_shares
        ) VALUES (
            'pos-no-q', 'active', 18.44, 18.44
        )
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_VENUE_COMMANDS_SCHEMA_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "VENUE_COMMANDS_ENTRY_Q_VERSION_MISSING_ACTIVE_EXPOSURE"
    assert result["missing_columns"] == []
    assert result["active_missing_q_version_count"] == 1
    assert result["active_missing_q_version_sample"][0]["position_id"] == "pos-no-q"


def _init_venue_order_truth_db(db_path: Path, *, command_state: str, venue_state: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            venue_order_id TEXT,
            intent_kind TEXT,
            state TEXT,
            side TEXT,
            size REAL,
            price REAL,
            position_id TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            order_status TEXT,
            chain_state TEXT,
            city TEXT,
            target_date TEXT,
            strategy_key TEXT
        );
        CREATE TABLE venue_order_facts (
            fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_order_id TEXT,
            command_id TEXT,
            state TEXT,
            remaining_size TEXT,
            matched_size TEXT,
            observed_at TEXT,
            ingested_at TEXT,
            local_sequence INTEGER
        );
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, venue_order_id, intent_kind, state, side, size, price,
            position_id, created_at, updated_at
        ) VALUES (
            'cmd-1', 'ord-1', 'ENTRY', ?, 'BUY', 9.0, 0.42, 'pos-1',
            '2026-07-04T00:00:00+00:00', '2026-07-04T00:01:00+00:00'
        )
        """,
        (command_state,),
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, order_status, chain_state, city, target_date, strategy_key
        ) VALUES (
            'pos-1', 'quarantined', 'rejected', 'entry_authority_quarantined',
            'Paris', '2026-07-04', 'center_bin_buy'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_order_facts (
            venue_order_id, command_id, state, remaining_size, matched_size,
            observed_at, ingested_at, local_sequence
        ) VALUES (
            'ord-1', 'cmd-1', ?, '9.0', '0.0',
            '2026-07-04T00:01:30+00:00',
            '2026-07-04T00:01:31+00:00', 2
        )
        """,
        (venue_state,),
    )
    conn.commit()
    conn.close()


def test_terminal_entry_command_venue_fact_conflict_status_rejects_resting_fact(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    _init_venue_order_truth_db(
        db_path,
        command_state="SUBMIT_REJECTED",
        venue_state="RESTING",
    )
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_TERMINAL_ENTRY_COMMAND_VENUE_FACT_CONFLICTS_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "TERMINAL_ENTRY_COMMAND_VENUE_FACT_CONFLICT"
    assert result["count"] == 1
    assert result["by_command_state"] == {"SUBMIT_REJECTED": 1}
    assert result["by_venue_state"] == {"RESTING": 1}
    assert result["sample"][0]["command_id"] == "cmd-1"
    assert result["sample"][0]["remaining_size"] == 9.0


def test_terminal_entry_command_venue_fact_conflict_status_accepts_terminal_fact(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    _init_venue_order_truth_db(
        db_path,
        command_state="CANCELLED",
        venue_state="CANCELLED",
    )
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_TERMINAL_ENTRY_COMMAND_VENUE_FACT_CONFLICTS_STATUS()

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["count"] == 0
    assert result["sample"] == []


def _init_monitor_cadence_db(db_path, *, monitor_at: datetime | None) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            shares REAL,
            chain_shares REAL,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """
    )
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, chain_shares, updated_at
        ) VALUES ('pos-1', 'active', 10.0, 10.0, ?)
        """,
        (now.isoformat(),),
    )
    if monitor_at is not None:
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, sequence_no, event_type, occurred_at
            ) VALUES ('evt-monitor', 'pos-1', 1, 'MONITOR_REFRESHED', ?)
            """,
            (monitor_at.isoformat(),),
        )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at
        ) VALUES ('evt-chain', 'pos-1', 2, 'CHAIN_SIZE_CORRECTED', ?)
        """,
        (now.isoformat(),),
    )
    conn.commit()
    conn.close()


def test_monitor_cadence_status_rejects_chain_sync_without_fresh_monitor(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    stale_monitor = datetime.now(timezone.utc) - timedelta(minutes=20)
    _init_monitor_cadence_db(db_path, monitor_at=stale_monitor)
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_MONITOR_CADENCE_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "MONITOR_CADENCE_STALE"
    assert result["open_position_count"] == 1
    assert result["position_current_updated_at_is_not_monitor_cadence"] is True


def test_monitor_cadence_status_accepts_fresh_monitor_refresh(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    fresh_monitor = datetime.now(timezone.utc) - timedelta(seconds=30)
    _init_monitor_cadence_db(db_path, monitor_at=fresh_monitor)
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_MONITOR_CADENCE_STATUS()

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["open_position_count"] == 1


def test_monitor_cadence_status_rejects_one_stale_position_when_another_is_fresh(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    fresh_monitor = datetime.now(timezone.utc) - timedelta(seconds=30)
    _init_monitor_cadence_db(db_path, monitor_at=fresh_monitor)
    conn = sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, chain_shares, updated_at
        ) VALUES ('pos-2', 'active', 3.0, 3.0, ?)
        """,
        (now.isoformat(),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_MONITOR_CADENCE_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "MONITOR_CADENCE_STALE"
    assert result["open_position_count"] == 2
    assert result["fresh_position_count"] == 1
    assert result["stale_or_missing_position_count"] == 1
    assert result["stale_or_missing_positions"][0]["position_id"] == "pos-2"


def test_monitor_cadence_status_reports_quarantined_chain_risk_without_blocking(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            shares REAL,
            chain_shares REAL,
            chain_state TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, chain_shares, chain_state
        ) VALUES ('pos-q', 'quarantined', 0.0, 4.0, 'entry_authority_quarantined')
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_MONITOR_CADENCE_STATUS()

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["open_position_count"] == 0
    assert result["non_monitor_chain_risk_position_count"] == 1
    assert result["non_monitor_chain_risk_role"] == (
        "chain_reconciliation_not_monitor_cadence"
    )
    assert result["non_monitor_chain_risk_positions"][0]["position_id"] == "pos-q"


def test_monitor_cadence_status_excludes_quarantined_zero_chain_risk(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            shares REAL,
            chain_shares REAL,
            chain_state TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, shares, chain_shares, chain_state
        ) VALUES ('pos-q-zero', 'quarantined', 10.0, 0.0, 'chain_confirmed_zero')
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_trade_db_path", lambda: db_path)

    result = _ORIGINAL_MONITOR_CADENCE_STATUS()

    assert result["ok"] is True
    assert result["open_position_count"] == 0
    assert result["non_monitor_chain_risk_position_count"] == 0


def test_healthcheck_is_not_healthy_when_monitor_cadence_stale(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_monitor_cadence_status",
        lambda: {
            "ok": False,
            "path": "/tmp/zeus_trades.db",
            "issue": "MONITOR_CADENCE_STALE",
        },
    )

    result = healthcheck.check()

    assert result["monitor_cadence_ok"] is False
    assert result["monitor_cadence_issue"] == "MONITOR_CADENCE_STALE"
    assert result["healthy"] is False


def _init_edli_queue_status_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE opportunity_event_processing (
            consumer_name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            processing_status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            claimed_at TEXT,
            processed_at TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (consumer_name, event_id)
        )
        """
    )
    return conn


def test_edli_queue_status_rejects_stale_processing_claim(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus-world.db"
    now = datetime.now(timezone.utc)
    conn = _init_edli_queue_status_db(db_path)
    conn.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, attempt_count,
            claimed_at, processed_at, last_error, updated_at
        ) VALUES ('edli_reactor_v1', 'evt-stale', 'processing', 1, ?, NULL, NULL, ?)
        """,
        (
            (now - timedelta(minutes=20)).isoformat(),
            (now - timedelta(minutes=20)).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_edli_world_db_path", lambda: db_path)

    result = _ORIGINAL_EDLI_QUEUE_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "EDLI_QUEUE_STALE_PROCESSING"
    assert result["stale_processing_count"] == 1
    assert result["claimable_work_count"] == 1


def test_edli_queue_status_accepts_future_retry_floor(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus-world.db"
    now = datetime.now(timezone.utc)
    conn = _init_edli_queue_status_db(db_path)
    conn.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, attempt_count,
            claimed_at, processed_at, last_error, updated_at
        ) VALUES ('edli_reactor_v1', 'evt-future', 'pending', 1, ?, NULL, NULL, ?)
        """,
        (
            (now + timedelta(minutes=10)).isoformat(),
            now.isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_edli_world_db_path", lambda: db_path)

    result = _ORIGINAL_EDLI_QUEUE_STATUS()

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["claimable_pending_count"] == 0
    assert result["claimable_work_count"] == 0


def test_healthcheck_is_not_healthy_when_edli_queue_stale(monkeypatch):
    monkeypatch.setattr(
        healthcheck,
        "_edli_queue_status",
        lambda: {
            "ok": False,
            "path": "/tmp/zeus-world.db",
            "issue": "EDLI_QUEUE_STALE_PROCESSING",
            "stale_processing_count": 1,
        },
    )

    result = healthcheck.check()

    assert result["edli_queue_ok"] is False
    assert result["edli_queue_issue"] == "EDLI_QUEUE_STALE_PROCESSING"
    assert result["healthy"] is False


def test_forecast_posteriors_schema_status_rejects_missing_runtime_layer(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            trade_authority_status TEXT NOT NULL DEFAULT 'BLOCKED'
                CHECK (trade_authority_status IN ('BLOCKED', 'BLOCKED'))
        )
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_forecast_db_path", lambda: db_path)

    result = _ORIGINAL_FORECAST_POSTERIORS_SCHEMA_STATUS()

    assert result["ok"] is False
    assert result["issue"] == "FORECAST_POSTERIORS_RUNTIME_LAYER_SCHEMA_DRIFT"
    assert result["runtime_layer_supported"] is False


def test_forecast_posteriors_schema_status_accepts_runtime_layer(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            runtime_layer TEXT NOT NULL DEFAULT 'live'
                CHECK (runtime_layer IN ('live'))
        )
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(healthcheck, "_forecast_db_path", lambda: db_path)

    result = _ORIGINAL_FORECAST_POSTERIORS_SCHEMA_STATUS()

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["runtime_layer_supported"] is True


def test_live_db_holder_status_blocks_long_lived_forecast_holder(monkeypatch, tmp_path):
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
            return _Result(0, f"p222\nn{db_path}\n")
        if cmd[:3] == ["ps", "-p", "222"]:
            return _Result(0, "2-00:00:00 /tmp/zeus/.venv/bin/python -m src.ingest.forecast_live_daemon\n")
        return _Result(1, "", "unexpected command")

    monkeypatch.setattr(healthcheck.subprocess, "run", _run)

    result = _ORIGINAL_LIVE_DB_HOLDER_STATUS()

    assert result["ok"] is False
    assert result["holders"][0]["known_live_owner"] is False
    assert result["unknown_long_lived_holders"][0]["command"] == "-m src.ingest.forecast_live_daemon"


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


def test_healthcheck_is_not_healthy_when_live_health_composite_is_degraded(monkeypatch, tmp_path):
    """Operator health must honor business-plane composite degradation."""
    status_path = tmp_path / "status_summary.json"
    composite_path = tmp_path / "live_health_composite.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload()))
    composite_path.write_text(json.dumps({
        "healthy": False,
        "status": "DEGRADED",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "failing_surfaces": ["status_summary"],
        "surfaces": {
            "status_summary": {
                "ok": False,
                "issue": "STATUS_SUMMARY_STALE(861s)",
            },
        },
    }))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_live_health_composite_path", lambda: composite_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["status_fresh"] is True
    assert result["live_health_composite_ok"] is False
    assert result["live_health_composite_issue"] == "STATUS_SUMMARY_STALE(861s)"
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_rejects_composite_healthy_when_execution_capability_is_unavailable(
    monkeypatch,
    tmp_path,
):
    """A healthy composite cannot override current entry/exit gate blockers."""
    status_path = tmp_path / "status_summary.json"
    composite_path = tmp_path / "live_health_composite.json"
    keeper_path = tmp_path / "venue-heartbeat-keeper.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        execution_capability={
            "entry": {
                "status": "unavailable",
                "global_allow_submit": False,
                "unavailable_components": ["heartbeat_supervisor", "risk_allocator_global"],
            },
            "exit": {
                "status": "unavailable",
                "global_allow_submit": False,
                "unavailable_components": ["heartbeat_supervisor"],
            },
        }
    )))
    composite_path.write_text(json.dumps({
        "healthy": True,
        "status": "HEALTHY",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "failing_surfaces": [],
        "surfaces": {},
    }))
    keeper_path.write_text(json.dumps({
        "health": "HEALTHY",
        "resting_order_safe": True,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_live_health_composite_path", lambda: composite_path)
    monkeypatch.setattr(healthcheck, "_venue_heartbeat_keeper_path", lambda: keeper_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["live_health_composite_ok"] is False
    assert result["live_health_composite_issue"] == (
        "LIVE_HEALTH_COMPOSITE_CONTRADICTS_CURRENT_FACTS(execution_capability)"
    )
    assert result["live_health_composite"]["contradictions"][0]["surface"] == (
        "execution_capability"
    )
    assert result["healthy"] is False


def test_healthcheck_rejects_composite_healthy_when_venue_heartbeat_is_lost(
    monkeypatch,
    tmp_path,
):
    """Daemon heartbeat health is not venue-heartbeat/order-safety health."""
    status_path = tmp_path / "status_summary.json"
    composite_path = tmp_path / "live_health_composite.json"
    keeper_path = tmp_path / "venue-heartbeat-keeper.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload()))
    composite_path.write_text(json.dumps({
        "healthy": True,
        "status": "HEALTHY",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "failing_surfaces": [],
        "surfaces": {},
    }))
    keeper_path.write_text(json.dumps({
        "health": "LOST",
        "resting_order_safe": False,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_live_health_composite_path", lambda: composite_path)
    monkeypatch.setattr(healthcheck, "_venue_heartbeat_keeper_path", lambda: keeper_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["live_health_composite_ok"] is False
    assert result["live_health_composite_issue"] == (
        "LIVE_HEALTH_COMPOSITE_CONTRADICTS_CURRENT_FACTS(venue_heartbeat)"
    )
    assert result["live_health_composite"]["contradictions"][0]["surface"] == "venue_heartbeat"
    assert result["healthy"] is False


def test_healthcheck_is_not_healthy_when_live_health_composite_is_stale(monkeypatch, tmp_path):
    """A prior healthy composite must not mask current business-plane staleness."""
    status_path = tmp_path / "status_summary.json"
    composite_path = tmp_path / "live_health_composite.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    stale_computed_at = (
        datetime.now(timezone.utc)
        - timedelta(seconds=healthcheck.LIVE_HEALTH_COMPOSITE_STALE_SECONDS + 30)
    ).isoformat()
    status_path.write_text(json.dumps(_status_payload()))
    composite_path.write_text(json.dumps({
        "healthy": True,
        "status": "HEALTHY",
        "failing_surfaces": [],
        "surfaces": {
            "status_summary": {
                "ok": True,
                "issue": None,
            },
        },
        "computed_at": stale_computed_at,
    }))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_live_health_composite_path", lambda: composite_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["status_fresh"] is True
    assert result["live_health_composite_ok"] is False
    assert result["live_health_composite_issue"].startswith("LIVE_HEALTH_COMPOSITE_STALE")
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_is_not_healthy_when_live_health_composite_time_is_unparseable(monkeypatch, tmp_path):
    """Composite freshness must be machine-provable, not assumed from a string."""
    status_path = tmp_path / "status_summary.json"
    composite_path = tmp_path / "live_health_composite.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload()))
    composite_path.write_text(json.dumps({
        "healthy": True,
        "status": "HEALTHY",
        "failing_surfaces": [],
        "surfaces": {},
        "computed_at": "not-a-timestamp",
    }))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_live_health_composite_path", lambda: composite_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["live_health_composite_ok"] is False
    assert result["live_health_composite_issue"] == "LIVE_HEALTH_COMPOSITE_UNPARSEABLE_COMPUTED_AT"
    assert result["healthy"] is False


def test_healthcheck_is_not_healthy_when_live_health_composite_time_is_typed(monkeypatch, tmp_path):
    """Typed computed_at values must fail closed instead of crashing healthcheck."""
    status_path = tmp_path / "status_summary.json"
    composite_path = tmp_path / "live_health_composite.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload()))
    composite_path.write_text(json.dumps({
        "healthy": True,
        "status": "HEALTHY",
        "failing_surfaces": [],
        "surfaces": {},
        "computed_at": 12345,
    }))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_live_health_composite_path", lambda: composite_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["live_health_composite_ok"] is False
    assert result["live_health_composite_issue"] == "LIVE_HEALTH_COMPOSITE_UNPARSEABLE_COMPUTED_AT"
    assert result["healthy"] is False


def test_healthcheck_is_not_healthy_when_live_health_composite_missing_required_keys(monkeypatch, tmp_path):
    """A composite file without freshness and shape proofs is not authority."""
    status_path = tmp_path / "status_summary.json"
    composite_path = tmp_path / "live_health_composite.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload()))
    composite_path.write_text(json.dumps({
        "healthy": True,
        "status": "HEALTHY",
        "failing_surfaces": [],
        "surfaces": {},
    }))
    _write_risk_state(risk_path)
    _write_no_trade_artifact(zeus_db_path)

    monkeypatch.setenv("ZEUS_MODE", "live")
    monkeypatch.setattr(healthcheck, "_status_path", lambda: status_path)
    monkeypatch.setattr(healthcheck, "_live_health_composite_path", lambda: composite_path)
    monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
    monkeypatch.setattr(healthcheck, "_zeus_db_path", lambda: zeus_db_path)

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["live_health_composite_ok"] is False
    assert result["live_health_composite_issue"] == (
        "LIVE_HEALTH_COMPOSITE_MISSING_KEYS(computed_at)"
    )
    assert result["healthy"] is False


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


def test_healthcheck_is_not_healthy_when_entry_execution_capability_is_unavailable(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(
        execution_capability={
            "entry": {
                "status": "unavailable",
                "global_allow_submit": False,
                "live_action_authorized": False,
                "components": [
                    {"component": "ws_gap_guard", "allowed": False, "reason": "message_received"}
                ],
            }
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

    assert result["entry_execution_capability_ok"] is False
    assert result["entry_execution_capability_issue"] == "LIVE_ENTRY_EXECUTION_UNAVAILABLE"
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_is_not_healthy_when_process_loaded_code_is_stale(monkeypatch, tmp_path):
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
        "_process_loaded_code_status",
        lambda launchd_contracts: {
            "ok": False,
            "issue": "PROCESS_LOADED_CODE_STALE",
            "stale": [{"name": "forecast_live", "pid": 202}],
            "unattested": [],
            "items": [],
        },
    )

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["process_code_ok"] is False
    assert result["process_code_issue"] == "PROCESS_LOADED_CODE_STALE"
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_is_not_healthy_when_status_summary_process_pid_is_stale(monkeypatch, tmp_path):
    """Relationship: fresh status JSON is not current truth if another pid wrote it."""
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(process={"pid": 11111})))
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

    assert result["pid"] == 123
    assert result["status_process_pid"] == 11111
    assert result["status_process_contract_ok"] is False
    assert result["status_process_contract_issue"] == "STATUS_SUMMARY_PROCESS_PID_MISMATCH"
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_accepts_status_summary_process_pid_matching_launchd(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    status_path.write_text(json.dumps(_status_payload(process={"pid": 123})))
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

    assert result["status_process_contract_ok"] is True
    assert result["healthy"] is True


def test_healthcheck_is_not_healthy_when_settlement_truth_is_stale(monkeypatch, tmp_path):
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
        "_settlement_truth_status",
        lambda: {
            "ok": False,
            "path": str(tmp_path / "zeus-forecasts.db"),
            "count": 10,
            "max_settled_at": "2026-05-11T19:59:13+00:00",
            "age_seconds": 500000.0,
            "issue": "SETTLEMENT_TRUTH_STALE",
        },
    )

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["settlement_truth_ok"] is False
    assert result["settlement_truth_issue"] == "SETTLEMENT_TRUTH_STALE"
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


def test_healthcheck_is_not_healthy_when_position_current_schema_drifts(
    monkeypatch, tmp_path
):
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
        "_position_current_schema_status",
        lambda: {
            "ok": False,
            "path": str(tmp_path / "zeus_trades.db"),
            "issue": "POSITION_CURRENT_MONITOR_FRESHNESS_SCHEMA_DRIFT",
            "missing_columns": ["last_monitor_prob_is_fresh"],
        },
    )

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["position_current_schema_ok"] is False
    assert (
        result["position_current_schema_issue"]
        == "POSITION_CURRENT_MONITOR_FRESHNESS_SCHEMA_DRIFT"
    )
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_is_not_healthy_when_venue_commands_schema_drifts(
    monkeypatch, tmp_path
):
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
        "_venue_commands_schema_status",
        lambda: {
            "ok": False,
            "path": str(tmp_path / "zeus_trades.db"),
            "issue": "VENUE_COMMANDS_SUBMIT_SCHEMA_DRIFT",
            "missing_columns": ["q_version"],
        },
    )

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["venue_commands_schema_ok"] is False
    assert result["venue_commands_schema_issue"] == "VENUE_COMMANDS_SUBMIT_SCHEMA_DRIFT"
    assert result["healthy"] is False
    assert healthcheck.exit_code_for(result) == 1


def test_healthcheck_is_not_healthy_when_venue_order_truth_conflicts(
    monkeypatch, tmp_path
):
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
        "_terminal_entry_command_venue_fact_conflicts_status",
        lambda: {
            "ok": False,
            "path": str(tmp_path / "zeus_trades.db"),
            "issue": "TERMINAL_ENTRY_COMMAND_VENUE_FACT_CONFLICT",
            "count": 1,
            "sample": [{"command_id": "cmd-1"}],
        },
    )

    class _Result:
        returncode = 0
        stdout = "123\t0\tcom.zeus.live-trading\n"

    monkeypatch.setattr(healthcheck.subprocess, "run", lambda *args, **kwargs: _Result())

    result = healthcheck.check()

    assert result["venue_order_truth_conflicts_ok"] is False
    assert (
        result["venue_order_truth_conflicts_issue"]
        == "TERMINAL_ENTRY_COMMAND_VENUE_FACT_CONFLICT"
    )
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


def test_healthcheck_accepts_fresh_previous_risk_contract_after_dependency_db_lock(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    now = datetime.now(timezone.utc)
    previous_checked_at = (now - timedelta(seconds=60)).isoformat()
    latest_checked_at = now.isoformat()
    status_path.write_text(json.dumps(_status_payload()))
    _write_risk_state(risk_path, checked_at=previous_checked_at)
    _append_risk_state(
        risk_path,
        checked_at=latest_checked_at,
        details={
            "status": "dependency_db_locked_previous_risk_level_preserved",
            "riskguard_degraded_reason": "dependency_db_locked",
            "previous_full_risk_level": "GREEN",
            "previous_full_risk_checked_at": previous_checked_at,
        },
    )
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

    assert result["riskguard_checked_at"] == latest_checked_at
    assert result["riskguard_fresh"] is True
    assert result["riskguard_contract_valid"] is True
    assert result["riskguard_contract_missing_keys"] == []
    assert result["riskguard_contract_source"] == "previous_full_row_after_dependency_db_lock"
    assert result["riskguard_contract_reference_checked_at"] == previous_checked_at
    assert result["healthy"] is True


def test_healthcheck_rejects_dependency_db_lock_row_when_previous_risk_contract_is_stale(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    risk_path = tmp_path / "risk_state.db"
    zeus_db_path = tmp_path / "zeus.db"
    now = datetime.now(timezone.utc)
    previous_checked_at = (now - timedelta(seconds=healthcheck.RISKGUARD_STALE_SECONDS + 30)).isoformat()
    latest_checked_at = now.isoformat()
    status_path.write_text(json.dumps(_status_payload()))
    _write_risk_state(risk_path, checked_at=previous_checked_at)
    _append_risk_state(
        risk_path,
        checked_at=latest_checked_at,
        details={
            "status": "dependency_db_locked_previous_risk_level_preserved",
            "riskguard_degraded_reason": "dependency_db_locked",
            "previous_full_risk_level": "GREEN",
            "previous_full_risk_checked_at": previous_checked_at,
        },
    )
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

    assert result["riskguard_fresh"] is True
    assert result["riskguard_contract_valid"] is False
    assert result["riskguard_contract_source"] == "previous_full_row_after_dependency_db_lock"
    assert result["riskguard_contract_reference_stale"] is True
    assert "execution_quality_level" in result["riskguard_contract_missing_keys"]
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
