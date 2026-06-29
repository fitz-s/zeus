#!/usr/bin/env python3
# Lifecycle: created=2026-06-18; last_reviewed=2026-06-28; last_reused=2026-06-28
# Purpose: Read-only preflight before restarting the live trading daemon.
# Reuse: Run immediately before loading com.zeus.live-trading or python -m src.main.
# Created: 2026-06-18
# Last reused or audited: 2026-06-28
# Authority basis: Zeus live-money restart proof gates in AGENTS.md.
"""Read-only live restart preflight.

This script does not submit, cancel, repair, or write any DB/state files.  It
separates restart evidence that is often conflated in operator summaries:
process state, configured submit authority, posterior freshness, pending-exit
actuation risk, and held-position belief coverage.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from check_data_pipeline_live_e2e import _connect_live_readonly
from src.config import STATE_DIR as DEFAULT_RUNTIME_STATE_DIR

SETTINGS_PATH = ROOT / "config" / "settings.json"
LIVE_TRADING_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.zeus.live-trading.plist"
CLOB_SIGNATURE_TYPE_SIDECAR_LABELS = (
    "price-channel-ingest",
    "post-trade-capital",
    "venue-heartbeat",
)


def _runtime_state_dir(plist_path: Path = LIVE_TRADING_PLIST_PATH) -> Path:
    """Resolve the canonical runtime state root used by the loaded live daemon.

    The preflight is often run from the deploy worktree.  In that shell,
    ``src.config.STATE_DIR`` points at the worktree's local ``state/`` unless
    ZEUS_PRIMARY_ROOT is exported.  The launchd job carries the runtime root, so
    use that same source before falling back to checkout-local state.
    """

    explicit = os.environ.get("ZEUS_LIVE_PREFLIGHT_STATE_DIR") or os.environ.get(
        "ZEUS_STATE_DIR"
    )
    if explicit:
        return Path(explicit).expanduser().resolve()

    primary_root = os.environ.get("ZEUS_PRIMARY_ROOT")
    if primary_root:
        return (Path(primary_root).expanduser().resolve() / "state")

    try:
        payload = plistlib.loads(plist_path.read_bytes())
        env = payload.get("EnvironmentVariables")
        if isinstance(env, dict):
            plist_root = env.get("ZEUS_PRIMARY_ROOT")
            if plist_root:
                return (Path(str(plist_root)).expanduser().resolve() / "state")
    except Exception:
        pass

    return Path(DEFAULT_RUNTIME_STATE_DIR).expanduser().resolve()


STATE_DIR = _runtime_state_dir()
TRADE_DB = Path(os.environ.get("ZEUS_TRADE_DB") or STATE_DIR / "zeus_trades.db")
WORLD_DB = Path(os.environ.get("ZEUS_WORLD_DB") or STATE_DIR / "zeus-world.db")
FORECAST_DB = Path(os.environ.get("ZEUS_FORECAST_DB") or STATE_DIR / "zeus-forecasts.db")
SCHEDULER_HEALTH_PATH = STATE_DIR / "scheduler_jobs_health.json"
FORECAST_LIVE_HEARTBEAT_PATH = STATE_DIR / "forecast-live-heartbeat.json"
DUST_SHARE_LIMIT = 0.01
SIDECAR_HEARTBEAT_MAX_AGE_SECONDS = 180.0
EXECUTION_FEASIBILITY_MAX_AGE_SECONDS = 180.0
EXECUTION_FEASIBILITY_CLOCK_SKEW_TOLERANCE_SECONDS = 5.0
EXECUTABLE_SUBSTRATE_MAX_AGE_SECONDS = 600.0
FORECAST_LIVE_HEARTBEAT_MAX_AGE_SECONDS = 120.0
REPLACEMENT_SIDECAR_RUNNING_MAX_AGE_SECONDS = 1800.0
COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS = 180.0
MONITOR_PROJECTION_MAX_AGE_SECONDS = 900.0
LIVE_ACTIONABLE_CERTIFICATE_LOOKBACK_HOURS = 48.0
LIVE_ACTIONABLE_CERTIFICATE_SAMPLE_LIMIT = 25
PREFLIGHT_VENUE_ORDER_AUDIT_LIMIT = 12
PREFLIGHT_VENUE_READ_TIMEOUT_SECONDS = 5.0
LIVE_MONEY_CERTIFICATE_PARENT_MODE_TYPES = (
    "ActionableTradeCertificate",
    "FinalIntentCertificate",
    "ExecutorExpressibilityCertificate",
    "PreSubmitRevalidationCertificate",
    "ExecutionCommandCertificate",
    "ExecutionReceiptCertificate",
    "LiveCapTransitionCertificate",
)
SIDECAR_HEARTBEATS = (
    ("substrate_observer_daemon", "daemon-heartbeat-substrate-observer.json"),
    ("price_channel_daemon", "daemon-heartbeat-price-channel-ingest.json"),
    ("post_trade_capital_daemon", "daemon-heartbeat-post-trade-capital.json"),
)
LIVE_ORDER_RESTART_RELEVANT_STATES = frozenset(
    {
        "DECISION_PROOF_ACCEPTED",
        "SUBMIT_PLAN_BUILT",
        "PRE_SUBMIT_REVALIDATED",
        "LIVE_CAP_RESERVED",
        "EXECUTION_COMMAND_CREATED",
        "PENDING_RECONCILE",
    }
)
PRE_SUBMIT_ECONOMIC_FIELDS = (
    "q_live",
    "q_lcb_5pct",
    "expected_edge",
    "size",
    "min_entry_price",
    "min_expected_profit_usd",
    "min_submit_edge_density",
    "qkernel_execution_economics",
)
TERMINAL_VENUE_COMMAND_STATES = frozenset(
    {"EXPIRED", "CANCELLED", "CANCELED", "REJECTED", "FAILED", "FILLED"}
)
TERMINAL_VENUE_FACT_STATES = frozenset(
    {"CANCEL_CONFIRMED", "CANCELED", "CANCELLED", "EXPIRED", "MATCHED", "FILLED"}
)
VENUE_POINT_MATCH_STATUSES = frozenset(
    {"LIVE", "OPEN", "RESTING", "PARTIAL", "PARTIALLY_MATCHED", "PARTIALLY_FILLED", "MATCHED", "FILLED", "MINED"}
)
VENUE_POINT_TERMINAL_NO_FILL_STATUSES = frozenset(
    {"CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}
)
HARD_TERMINAL_POSITION_PHASES = frozenset(
    {"voided", "settled", "economically_closed", "admin_closed"}
)
HARD_TERMINAL_POSITION_EVENT_TYPES = frozenset(
    {
        "ADMIN_VOIDED",
        "ENTRY_ORDER_VOIDED",
        "POSITION_VOIDED",
        "POSITION_SETTLED",
        "SETTLED",
        "ECONOMICALLY_CLOSED",
    }
)
OPEN_POSITION_PHASES = frozenset({"active", "day0_window", "pending_exit"})
REPLACEMENT_SCHEDULER_HEALTH_JOBS = (
    "bayes_precision_fusion_capture",
    "replacement_forecast_download",
    "replacement_forecast_live_materialize",
)
REPLACEMENT_HEARTBEAT_JOBS = (
    "replacement_forecast_download",
    "replacement_forecast_live_materialize",
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    evidence: dict[str, Any]


def _connect_live_ro():
    return _connect_live_readonly(
        trade_db=TRADE_DB,
        world_db=WORLD_DB,
        forecasts_db=FORECAST_DB,
    )


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _live_main_processes() -> list[str]:
    try:
        out = subprocess.check_output(["ps", "-axo", "pid,command"], text=True)
    except Exception:
        return []
    rows: list[str] = []
    for line in out.splitlines():
        if "python" in line and ("-m src.main" in line or "src.main" in line):
            if "check_live_restart_preflight" not in line:
                rows.append(line.strip())
    return rows


def _live_trading_launchagent_installed_check() -> CheckResult:
    evidence: dict[str, Any] = {
        "plist_path": str(LIVE_TRADING_PLIST_PATH),
        "expected_label": "com.zeus.live-trading",
        "expected_module": "src.main",
    }
    if not LIVE_TRADING_PLIST_PATH.exists():
        return CheckResult(
            "live_trading_launchagent_installed",
            False,
            "active live-trading LaunchAgent plist is missing",
            evidence,
        )
    try:
        with LIVE_TRADING_PLIST_PATH.open("rb") as handle:
            payload = plistlib.load(handle)
    except Exception as exc:  # noqa: BLE001
        evidence["error"] = str(exc)
        return CheckResult(
            "live_trading_launchagent_installed",
            False,
            "active live-trading LaunchAgent plist is unreadable",
            evidence,
        )
    label = payload.get("Label")
    args = payload.get("ProgramArguments")
    working_directory = payload.get("WorkingDirectory")
    args_list = [str(arg) for arg in args] if isinstance(args, list) else []
    evidence.update(
        {
            "label": label,
            "program_arguments": args_list,
            "working_directory": working_directory,
        }
    )
    module_ok = "-m" in args_list and "src.main" in args_list
    ok = label == "com.zeus.live-trading" and module_ok
    return CheckResult(
        "live_trading_launchagent_installed",
        ok,
        "active live-trading LaunchAgent targets src.main"
        if ok
        else "active live-trading LaunchAgent does not target com.zeus.live-trading src.main",
        evidence,
    )


def _settings() -> dict[str, Any]:
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except Exception:
        return {}


def _live_trading_python_executable() -> str:
    """Return the Python executable launchd will use for ``src.main``.

    Preflight must validate boot with the same interpreter as the live daemon.
    Do not inspect or echo plist EnvironmentVariables here; they may contain
    secrets and are not needed to resolve ProgramArguments[0].
    """

    try:
        payload = plistlib.loads(LIVE_TRADING_PLIST_PATH.read_bytes())
        args = payload.get("ProgramArguments")
        if isinstance(args, list) and args:
            executable = str(args[0]).strip()
            if executable:
                return executable
    except Exception:
        pass
    return sys.executable


def _plist_env_value(path: Path, key: str) -> tuple[str | None, str | None]:
    """Read one non-secret launchd environment value from a plist."""
    try:
        payload = plistlib.loads(path.read_bytes())
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    env = payload.get("EnvironmentVariables")
    if not isinstance(env, dict):
        return None, "EnvironmentVariables missing or not a dictionary"
    value = env.get(key)
    return (str(value).strip() if value is not None else None), None


def _live_trading_plist_env_value(key: str) -> tuple[str | None, str | None]:
    """Read one non-secret launchd environment value from the live-trading plist."""

    return _plist_env_value(LIVE_TRADING_PLIST_PATH, key)


def _launchagent_plist_path_for_label(label: str) -> Path:
    if label == "live-trading":
        return LIVE_TRADING_PLIST_PATH
    return Path.home() / "Library" / "LaunchAgents" / f"com.zeus.{label}.plist"


def _clob_signature_type_config_check(*, required: bool) -> CheckResult:
    """Verify CLOB-using live money daemons have an explicit V2 signature type."""

    key = "POLYMARKET_CLOB_V2_SIGNATURE_TYPE"
    allowed_values = {"0", "1", "2", "3"}
    labels = ("live-trading", *CLOB_SIGNATURE_TYPE_SIDECAR_LABELS)
    items: list[dict[str, Any]] = []
    live_value: str | None = None
    live_error: str | None = None
    for label in labels:
        path = _launchagent_plist_path_for_label(label)
        value, error = _plist_env_value(path, key)
        if label == "live-trading":
            live_value = value
            live_error = error
        item: dict[str, Any] = {
            "label": label,
            "plist_path": str(path),
            "present": bool(value),
            "supported": bool(value in allowed_values) if value else False,
        }
        if value:
            item["configured_value"] = value
        if error:
            item["plist_error"] = error
        items.append(item)

    evidence: dict[str, Any] = {
        "plist_path": str(LIVE_TRADING_PLIST_PATH),
        "required": required,
        "present": bool(live_value),
        "allowed_values": sorted(allowed_values),
        "items": items,
    }
    if live_value:
        evidence["configured_value"] = live_value
    if live_error:
        evidence["plist_error"] = live_error

    if not required:
        return CheckResult(
            "clob_signature_type_config",
            True,
            "explicit CLOB V2 signature type is not required while live submit is not armed",
            evidence,
        )

    failed = [
        item
        for item in items
        if (not item["present"]) or (not item["supported"])
    ]
    if failed:
        missing = [item["label"] for item in failed if not item["present"]]
        unsupported = [item["label"] for item in failed if item["present"] and not item["supported"]]
        issue_parts: list[str] = []
        if missing:
            issue_parts.append(f"missing: {', '.join(missing)}")
        if unsupported:
            issue_parts.append(f"unsupported: {', '.join(unsupported)}")
        return CheckResult(
            "clob_signature_type_config",
            False,
            "live submit requires explicit supported POLYMARKET_CLOB_V2_SIGNATURE_TYPE "
            f"in CLOB money-path LaunchAgents ({'; '.join(issue_parts)})",
            evidence,
        )

    return CheckResult(
        "clob_signature_type_config",
        True,
        "CLOB money-path LaunchAgents have explicit supported CLOB V2 signature types",
        evidence,
    )


def _qkernel_spine_cutover_check(cfg: dict[str, Any]) -> CheckResult:
    flags = cfg.get("feature_flags") if isinstance(cfg.get("feature_flags"), dict) else {}
    enabled = flags.get("qkernel_spine_enabled")
    ok = enabled is True
    return CheckResult(
        "qkernel_spine_cutover",
        ok,
        "qkernel spine is enabled" if ok else "qkernel spine is not enabled for live restart",
        {
            "settings_path": str(SETTINGS_PATH),
            "feature_flags.qkernel_spine_enabled": enabled,
        },
    )


def _src_main_boot_guard_check() -> CheckResult:
    python_executable = _live_trading_python_executable()
    command = [
        python_executable,
        "-m",
        "src.main",
        "--validate-boot",
        "--settings-path",
        str(SETTINGS_PATH),
    ]
    evidence: dict[str, Any] = {
        "command": command,
        "cwd": str(ROOT),
        "settings_path": str(SETTINGS_PATH),
        "python_source": (
            "launchagent_program_arguments"
            if python_executable != sys.executable
            else "current_process"
        ),
    }
    try:
        proc = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        evidence["timeout_seconds"] = exc.timeout
        evidence["stdout_tail"] = (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else ""
        evidence["stderr_tail"] = (exc.stderr or "")[-2000:] if isinstance(exc.stderr, str) else ""
        return CheckResult(
            "src_main_boot_guards",
            False,
            "src.main --validate-boot timed out before restart",
            evidence,
        )
    except Exception as exc:  # noqa: BLE001
        evidence["error"] = str(exc)
        return CheckResult(
            "src_main_boot_guards",
            False,
            "src.main --validate-boot could not run",
            evidence,
        )
    evidence.update(
        {
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
        }
    )
    ok = proc.returncode == 0
    return CheckResult(
        "src_main_boot_guards",
        ok,
        "src.main boot guards pass"
        if ok
        else "src.main boot guards fail; restart would crash before scheduler",
        evidence,
    )


def _family_portfolio_single_leg_check() -> CheckResult:
    try:
        from src.strategy.family_exclusive_dedup import (
            ENV_FAMILY_PORTFOLIO_MAX_LEGS_LIVE,
            _family_portfolio_max_legs,
        )

        max_legs = _family_portfolio_max_legs()
        raw = os.environ.get(ENV_FAMILY_PORTFOLIO_MAX_LEGS_LIVE)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "family_portfolio_single_leg_cutover",
            False,
            "family portfolio max-legs check failed",
            {"error": str(exc)},
        )
    ok = max_legs == 1
    return CheckResult(
        "family_portfolio_single_leg_cutover",
        ok,
        (
            "live family portfolio execution is constrained to one leg"
            if ok
            else "live family portfolio max_legs exceeds 1 without portfolio execution state machine"
        ),
        {
            "env": ENV_FAMILY_PORTFOLIO_MAX_LEGS_LIVE,
            "raw_value": raw,
            "effective_max_legs": max_legs,
        },
    )


def _qlcb_reliability_artifact_check() -> CheckResult:
    try:
        from src.decision import qlcb_reliability_guard as qlcb_guard

        qlcb_guard._QLCB_OOF_RELIABILITY_PATH = str(STATE_DIR / "qlcb_oof_reliability.json")
        qlcb_guard.reset_reliability_cache()
        evidence = qlcb_guard.reliability_artifact_status()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "qlcb_reliability_artifact",
            False,
            "qLCB reliability artifact health check failed",
            {"error": str(exc)},
        )
    status = str(evidence.get("status") or "")
    ok = status == "ACTIVE_VALID"
    return CheckResult(
        "qlcb_reliability_artifact",
        ok,
        (
            "qLCB reliability artifact is active-valid"
            if ok
            else "qLCB reliability artifact is not active-valid for live restart"
        ),
        evidence,
    )


def _parse_dt(raw: object) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    try:
        row = conn.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _table_columns(conn: sqlite3.Connection, schema: str, table: str) -> set[str]:
    if not _table_exists(conn, schema, table):
        return set()
    try:
        return {
            str(row["name"])
            for row in conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()
        }
    except sqlite3.Error:
        return set()


def _open_exposure_identity(row: sqlite3.Row) -> dict[str, Any]:
    tokens = {
        str(row[key] or "").strip()
        for key in ("token_id", "no_token_id")
        if key in row.keys()
    }
    tokens.discard("")
    return {
        "position_id": row["position_id"],
        "phase": row["phase"],
        "city": row["city"],
        "target_date": row["target_date"],
        "temperature_metric": row["temperature_metric"],
        "bin_label": row["bin_label"],
        "direction": row["direction"],
        "condition_id": str(row["condition_id"] or "").strip() if "condition_id" in row.keys() else "",
        "tokens": sorted(tokens),
    }


def _freshness_predicate_for_exposure(
    *,
    columns: set[str],
    exposure: dict[str, Any],
    token_columns: tuple[str, ...],
    include_condition_id: bool = True,
) -> tuple[str, tuple[Any, ...]] | None:
    clauses: list[str] = []
    params: list[Any] = []
    condition_id = str(exposure.get("condition_id") or "").strip()
    if include_condition_id and condition_id and "condition_id" in columns:
        clauses.append("condition_id = ?")
        params.append(condition_id)
    tokens = [token for token in exposure.get("tokens", []) if token]
    available_token_columns = [column for column in token_columns if column in columns]
    if tokens and available_token_columns:
        placeholders = ",".join("?" for _ in tokens)
        for column in available_token_columns:
            clauses.append(f"{column} IN ({placeholders})")
            params.extend(tokens)
    if not clauses:
        return None
    return "(" + " OR ".join(clauses) + ")", tuple(params)


def _exposure_stub(exposure: dict[str, Any]) -> dict[str, Any]:
    return {
        "position_id": exposure.get("position_id"),
        "phase": exposure.get("phase"),
        "city": exposure.get("city"),
        "target_date": exposure.get("target_date"),
        "temperature_metric": exposure.get("temperature_metric"),
        "bin_label": exposure.get("bin_label"),
        "direction": exposure.get("direction"),
        "condition_id": exposure.get("condition_id"),
        "tokens": exposure.get("tokens", []),
    }


def _sidecar_heartbeat_check(name: str, filename: str) -> CheckResult:
    path = STATE_DIR / filename
    evidence: dict[str, Any] = {
        "path": str(path),
        "max_age_seconds": SIDECAR_HEARTBEAT_MAX_AGE_SECONDS,
    }
    if not path.exists():
        return CheckResult(
            f"{name}_heartbeat",
            False,
            "sidecar heartbeat file is missing",
            evidence,
        )
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        evidence["error"] = str(exc)
        return CheckResult(
            f"{name}_heartbeat",
            False,
            "sidecar heartbeat file is unreadable",
            evidence,
        )
    alive_at = _parse_dt(payload.get("alive_at") or payload.get("generated_at") or payload.get("timestamp"))
    evidence["payload"] = payload
    evidence["alive_at"] = alive_at.isoformat() if alive_at else None
    if alive_at is None:
        return CheckResult(
            f"{name}_heartbeat",
            False,
            "sidecar heartbeat timestamp is invalid",
            evidence,
        )
    age = (datetime.now(timezone.utc) - alive_at).total_seconds()
    evidence["age_seconds"] = age
    ok = 0.0 <= age <= SIDECAR_HEARTBEAT_MAX_AGE_SECONDS
    return CheckResult(
        f"{name}_heartbeat",
        ok,
        "sidecar heartbeat is fresh" if ok else "sidecar heartbeat is stale",
        evidence,
    )


def _sidecar_heartbeat_checks() -> list[CheckResult]:
    return [_sidecar_heartbeat_check(name, filename) for name, filename in SIDECAR_HEARTBEATS]


def _collateral_snapshot_freshness_check() -> CheckResult:
    evidence: dict[str, Any] = {
        "trade_db": str(TRADE_DB),
        "max_age_seconds": COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS,
    }
    try:
        with _connect_live_ro() as conn:
            if not _table_exists(conn, "main", "collateral_ledger_snapshots"):
                return CheckResult(
                    "collateral_snapshot_freshness",
                    False,
                    "collateral ledger snapshot table is missing",
                    evidence,
                )
            row = conn.execute(
                """
                SELECT id, captured_at, authority_tier, pusd_balance_micro, pusd_allowance_micro
                  FROM collateral_ledger_snapshots
                 ORDER BY id DESC
                 LIMIT 1
                """
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        evidence["error"] = str(exc)
        return CheckResult(
            "collateral_snapshot_freshness",
            False,
            "collateral ledger snapshot could not be read",
            evidence,
        )
    if row is None:
        return CheckResult(
            "collateral_snapshot_freshness",
            False,
            "collateral ledger has no snapshot rows",
            evidence,
        )
    payload = dict(row)
    captured_at = _parse_dt(payload.get("captured_at"))
    evidence["latest_snapshot"] = payload
    evidence["captured_at"] = captured_at.isoformat() if captured_at else None
    if captured_at is None:
        return CheckResult(
            "collateral_snapshot_freshness",
            False,
            "latest collateral snapshot timestamp is invalid",
            evidence,
        )
    age = (datetime.now(timezone.utc) - captured_at).total_seconds()
    evidence["age_seconds"] = age
    authority_tier = str(payload.get("authority_tier") or "")
    ok = (
        authority_tier != "DEGRADED"
        and 0.0 <= age <= COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS
    )
    return CheckResult(
        "collateral_snapshot_freshness",
        ok,
        "collateral ledger snapshot is fresh"
        if ok
        else "collateral ledger snapshot is stale, degraded, or future-dated",
        evidence,
    )


def _edli_live_order_presubmit_shape_check() -> CheckResult:
    """Block restart when active live-order aggregates predate submit economics."""

    evidence: dict[str, Any] = {
        "world_db": str(WORLD_DB),
        "required_fields": list(PRE_SUBMIT_ECONOMIC_FIELDS),
    }
    if not WORLD_DB.exists():
        return CheckResult(
            "edli_live_order_presubmit_shape",
            True,
            "world DB absent; no EDLI live-order aggregate rows to inspect",
            evidence,
        )
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{WORLD_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not {"edli_live_order_events", "edli_live_order_projection"}.issubset(tables):
            evidence["tables_present"] = sorted(tables)
            return CheckResult(
                "edli_live_order_presubmit_shape",
                True,
                "EDLI live-order aggregate tables absent",
                evidence,
            )
        field_checks = " OR ".join(
            f"json_type(pre.payload_json, '$.{field}') IS NULL"
            for field in PRE_SUBMIT_ECONOMIC_FIELDS
        )
        state_placeholders = ",".join("?" for _ in LIVE_ORDER_RESTART_RELEVANT_STATES)
        risk_predicate = f"""
          pre.rn = 1
          AND proj.current_state IN ({state_placeholders})
          AND ({field_checks})
        """
        params = tuple(sorted(LIVE_ORDER_RESTART_RELEVANT_STATES))
        risk_cte = f"""
            WITH latest_pre AS (
                SELECT
                    aggregate_id,
                    payload_json,
                    occurred_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY aggregate_id
                        ORDER BY event_sequence DESC
                    ) AS rn
                FROM edli_live_order_events
                WHERE event_type = 'PreSubmitRevalidated'
            ),
            risk AS (
                SELECT
                    proj.aggregate_id,
                    proj.current_state,
                    proj.last_sequence,
                    proj.pending_reconcile,
                    proj.venue_order_id,
                    pre.occurred_at,
                    json_extract(pre.payload_json, '$.event_id') AS event_id,
                    json_extract(pre.payload_json, '$.direction') AS direction,
                    json_extract(pre.payload_json, '$.limit_price') AS limit_price
                FROM latest_pre pre
                JOIN edli_live_order_projection proj
                  ON proj.aggregate_id = pre.aggregate_id
                WHERE {risk_predicate}
            ),
            current_command AS (
                SELECT
                    risk.*,
                    cmd.event_sequence AS command_sequence,
                    json_extract(cmd.payload_json, '$.execution_command_id') AS execution_command_id
                FROM risk
                JOIN edli_live_order_events cmd
                  ON cmd.aggregate_id = risk.aggregate_id
                 AND cmd.event_type = 'ExecutionCommandCreated'
                 AND cmd.event_sequence = risk.last_sequence
            )
        """
        samples = conn.execute(
            f"""
            {risk_cte}
            SELECT
                aggregate_id,
                current_state,
                pending_reconcile,
                venue_order_id,
                occurred_at,
                event_id,
                direction,
                limit_price
            FROM risk
            ORDER BY occurred_at DESC
            LIMIT 25
            """,
            params,
        ).fetchall()
        missing_count = int(
            conn.execute(
                f"""
                WITH latest_pre AS (
                    SELECT
                        aggregate_id,
                        payload_json,
                        ROW_NUMBER() OVER (
                            PARTITION BY aggregate_id
                            ORDER BY event_sequence DESC
                        ) AS rn
                    FROM edli_live_order_events
                    WHERE event_type = 'PreSubmitRevalidated'
                )
                SELECT COUNT(*)
                FROM latest_pre pre
                JOIN edli_live_order_projection proj
                  ON proj.aggregate_id = pre.aggregate_id
                WHERE {risk_predicate}
                """,
                params,
            ).fetchone()[0]
            or 0
        )
        unsafe_count = missing_count
        unsafe_samples = samples
    except sqlite3.Error as exc:
        evidence["error"] = str(exc)
        return CheckResult(
            "edli_live_order_presubmit_shape",
            False,
            "could not inspect EDLI live-order aggregate rows",
            evidence,
        )
    finally:
        if conn is not None:
            conn.close()

    evidence["missing_count"] = missing_count
    evidence["boot_recoverable_count"] = 0
    evidence["unsubmitted_ghost_recoverable_count"] = 0
    evidence["terminal_command_recoverable_count"] = 0
    evidence["restart_policy"] = (
        "fail_closed_restart_relevant_presubmit_requires_current_entry_economics"
    )
    evidence["unsafe_count"] = unsafe_count
    evidence["samples"] = [dict(row) for row in unsafe_samples]
    return CheckResult(
        "edli_live_order_presubmit_shape",
        unsafe_count == 0,
        "restart-relevant live-order aggregates carry pre-submit economics"
        if missing_count == 0
        else (
            "restart-relevant live-order aggregates predate pre-submit economics"
        ),
        evidence,
    )


def _live_actionable_certificate_semantics_check() -> CheckResult:
    """Re-verify LIVE ActionableTradeCertificate rows with current money law.

    Historical certificates are immutable receipts. A verifier hotfix can make old rows
    fail current money law, and that is important audit evidence, but it is restart-blocking
    only when a currently restart-relevant entry command still references the certificate's
    event/token. Once chain exposure exists, held-position monitoring uses current
    belief, price, and lifecycle evidence rather than re-adjudicating the entry receipt.
    """

    evidence: dict[str, Any] = {
        "world_db": str(WORLD_DB),
        "lookback_hours": LIVE_ACTIONABLE_CERTIFICATE_LOOKBACK_HOURS,
        "certificate_type": "ActionableTradeCertificate",
    }
    if not WORLD_DB.exists():
        return CheckResult(
            "live_actionable_certificate_semantics",
            True,
            "world DB absent; no actionable certificates to inspect",
            evidence,
        )
    since = datetime.now(timezone.utc) - timedelta(
        hours=LIVE_ACTIONABLE_CERTIFICATE_LOOKBACK_HOURS
    )
    try:
        from src.decision_kernel.errors import CertificateVerificationError
        from src.decision_kernel.verifier import _verify_actionable_payload
        from src.state.decision_integrity_quarantine import (
            DECISION_CERTIFICATES_TABLE,
            REASON_INVALID_LIVE_ACTIONABLE,
        )
    except Exception as exc:  # noqa: BLE001
        evidence["error"] = str(exc)
        return CheckResult(
            "live_actionable_certificate_semantics",
            False,
            "could not load current actionable certificate verifier",
            evidence,
        )
    quarantined_hashes = _decision_certificate_quarantine_hashes(
        table_name=DECISION_CERTIFICATES_TABLE,
        reason_code=REASON_INVALID_LIVE_ACTIONABLE,
    )
    evidence["quarantined_count"] = len(quarantined_hashes)
    restart_relevant_commands = _restart_relevant_entry_command_index()
    evidence["restart_relevant_entry_command_count"] = sum(
        len(items) for items in restart_relevant_commands.values()
    )
    evidence["restart_relevant_entry_commands"] = [
        item
        for items in restart_relevant_commands.values()
        for item in items[:LIVE_ACTIONABLE_CERTIFICATE_SAMPLE_LIMIT]
    ][:LIVE_ACTIONABLE_CERTIFICATE_SAMPLE_LIMIT]
    try:
        conn = sqlite3.connect(f"file:{WORLD_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "main", "decision_certificates"):
            return CheckResult(
                "live_actionable_certificate_semantics",
                True,
                "decision_certificates table absent",
                evidence,
            )
        rows = conn.execute(
            """
            SELECT
                certificate_id,
                certificate_hash,
                decision_time,
                payload_json
              FROM decision_certificates
             WHERE certificate_type = 'ActionableTradeCertificate'
               AND mode = 'LIVE'
               AND verifier_status = 'VERIFIED'
               AND datetime(decision_time) >= datetime(?)
             ORDER BY datetime(decision_time) DESC, certificate_id DESC
            """,
            (since.isoformat(),),
        ).fetchall()
    except sqlite3.Error as exc:
        evidence["error"] = str(exc)
        return CheckResult(
            "live_actionable_certificate_semantics",
            False,
            "could not inspect actionable certificate rows",
            evidence,
        )
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass

    risky: list[dict[str, Any]] = []
    historical_risky: list[dict[str, Any]] = []
    risky_count = 0
    historical_risky_count = 0
    quarantined_risky_count = 0
    checked = 0
    for row in rows:
        checked += 1
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
            if not isinstance(payload, dict):
                raise CertificateVerificationError("actionable payload must be object")
            _verify_actionable_payload(type("_PayloadCarrier", (), {"payload": payload})())
        except Exception as exc:  # noqa: BLE001
            cert_hash = str(row["certificate_hash"] or "")
            restart_relevant = _payload_matches_restart_relevant_entry_command(
                payload,
                restart_relevant_commands,
            )
            if cert_hash in quarantined_hashes and not restart_relevant:
                quarantined_risky_count += 1
                continue
            sample = {
                "certificate_id": row["certificate_id"],
                "certificate_hash": cert_hash,
                "decision_time": row["decision_time"],
                "risk": "live_actionable_certificate_fails_current_verifier",
                "reason": str(exc),
                "restart_relevant": restart_relevant,
                "quarantined": cert_hash in quarantined_hashes,
            }
            if isinstance(payload, dict):
                sample.update(
                    {
                        "event_id": payload.get("event_id"),
                        "city": payload.get("city"),
                        "target_date": payload.get("target_date"),
                        "temperature_metric": payload.get("temperature_metric"),
                        "direction": payload.get("direction"),
                        "bin_label": payload.get("bin_label"),
                        "token_id": payload.get("token_id"),
                        "q_live": payload.get("q_live"),
                        "q_lcb_5pct": payload.get("q_lcb_5pct"),
                    }
                )
            if restart_relevant:
                risky_count += 1
                sample["matched_restart_commands"] = restart_relevant_commands.get(
                    str(payload.get("event_id") or ""),
                    [],
                )[:LIVE_ACTIONABLE_CERTIFICATE_SAMPLE_LIMIT]
                if len(risky) < LIVE_ACTIONABLE_CERTIFICATE_SAMPLE_LIMIT:
                    risky.append(sample)
            else:
                historical_risky_count += 1
                if len(historical_risky) < LIVE_ACTIONABLE_CERTIFICATE_SAMPLE_LIMIT:
                    historical_risky.append(sample)
    evidence["checked_count"] = checked
    evidence["risky_count"] = risky_count
    evidence["historical_risky_count"] = historical_risky_count
    evidence["quarantined_risky_count"] = quarantined_risky_count
    evidence["risky"] = risky
    evidence["historical_risky"] = historical_risky
    return CheckResult(
        "live_actionable_certificate_semantics",
        not risky,
        "restart-relevant actionable certificates verify under current qkernel money law"
        if not risky
        else "restart-relevant actionable certificates fail current qkernel money law",
        evidence,
    )


def _edli_event_id_from_decision_id(decision_id: object) -> str:
    parts = str(decision_id or "").split(":")
    if len(parts) >= 2 and parts[0] == "edli_exec_cmd":
        return parts[1]
    return ""


def _restart_relevant_entry_command_index() -> dict[str, list[dict[str, Any]]]:
    """Current nonterminal ENTRY commands keyed by EDLI event id."""

    if not TRADE_DB.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{TRADE_DB}?mode=ro", uri=True, timeout=1)
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "main", "venue_commands"):
            return {}
        terminal_placeholders = ",".join("?" for _ in TERMINAL_VENUE_COMMAND_STATES)
        rows = conn.execute(
            f"""
            SELECT command_id, position_id, decision_id, token_id, state, venue_order_id,
                   created_at, updated_at
              FROM venue_commands
             WHERE UPPER(COALESCE(intent_kind, '')) = 'ENTRY'
               AND UPPER(COALESCE(side, '')) = 'BUY'
               AND UPPER(COALESCE(state, '')) NOT IN ({terminal_placeholders})
               AND COALESCE(venue_order_id, '') != ''
             ORDER BY datetime(updated_at) DESC, command_id DESC
            """,
            tuple(sorted(TERMINAL_VENUE_COMMAND_STATES)),
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass
    index: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event_id = _edli_event_id_from_decision_id(row["decision_id"])
        if not event_id:
            continue
        item = dict(row)
        item["event_id"] = event_id
        index.setdefault(event_id, []).append(item)
    return index


def _restart_relevant_entry_commands_for_venue_audit() -> list[dict[str, Any]]:
    """Current nonterminal ENTRY orders whose venue point truth must match local truth."""

    if not TRADE_DB.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{TRADE_DB}?mode=ro", uri=True, timeout=1)
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "main", "venue_commands"):
            return []
        terminal_placeholders = ",".join("?" for _ in TERMINAL_VENUE_COMMAND_STATES)
        fact_join = ""
        fact_select = (
            "NULL AS latest_fact_state, NULL AS latest_fact_matched_size, "
            "NULL AS latest_fact_remaining_size, NULL AS latest_fact_observed_at"
        )
        if _table_exists(conn, "main", "venue_order_facts"):
            fact_select = (
                "lf.state AS latest_fact_state, lf.matched_size AS latest_fact_matched_size, "
                "lf.remaining_size AS latest_fact_remaining_size, lf.observed_at AS latest_fact_observed_at"
            )
            fact_join = """
              LEFT JOIN (
                SELECT command_id, venue_order_id, state, matched_size, remaining_size, observed_at
                  FROM (
                    SELECT vof.command_id, vof.venue_order_id, vof.state, vof.matched_size,
                           vof.remaining_size, vof.observed_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY vof.command_id, vof.venue_order_id
                               ORDER BY datetime(vof.observed_at) DESC, vof.rowid DESC
                           ) AS rn
                      FROM venue_order_facts vof
                  )
                 WHERE rn = 1
              ) lf
                ON lf.command_id = cmd.command_id
               AND lf.venue_order_id = cmd.venue_order_id
            """
        rows = conn.execute(
            f"""
            SELECT cmd.command_id, cmd.position_id, cmd.decision_id, cmd.token_id,
                   cmd.state, cmd.venue_order_id, cmd.size, cmd.price,
                   cmd.created_at, cmd.updated_at,
                   {fact_select}
              FROM venue_commands cmd
              {fact_join}
             WHERE UPPER(COALESCE(cmd.intent_kind, '')) = 'ENTRY'
               AND UPPER(COALESCE(cmd.side, '')) = 'BUY'
               AND UPPER(COALESCE(cmd.state, '')) NOT IN ({terminal_placeholders})
               AND COALESCE(cmd.venue_order_id, '') != ''
             ORDER BY datetime(cmd.updated_at) DESC, cmd.command_id DESC
             LIMIT ?
            """,
            (*tuple(sorted(TERMINAL_VENUE_COMMAND_STATES)), PREFLIGHT_VENUE_ORDER_AUDIT_LIMIT),
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass


def _preflight_venue_adapter():
    from src.data.polymarket_client import PolymarketClient

    client = PolymarketClient(public_http_timeout=PREFLIGHT_VENUE_READ_TIMEOUT_SECONDS)
    return client, client._ensure_v2_adapter()


def _venue_payload(value: object | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        payload = dict(value)
    else:
        raw = getattr(value, "raw", None)
        payload = dict(raw) if isinstance(raw, dict) else dict(getattr(value, "__dict__", {}) or {})
    status = getattr(value, "status", None)
    if status not in (None, "") and not (payload.get("status") or payload.get("state")):
        payload["status"] = str(status)
    order_id = getattr(value, "order_id", None)
    if order_id not in (None, "") and not (payload.get("id") or payload.get("orderID") or payload.get("order_id")):
        payload["orderID"] = str(order_id)
    return payload


def _venue_status(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "NOT_FOUND"
    return str(payload.get("status") or payload.get("state") or "").strip().upper()


def _decimal_float(value: object) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _payload_matched_size(payload: dict[str, Any] | None) -> float | None:
    if not payload:
        return None
    for key in ("size_matched", "matched_size", "matchedAmount", "matched_amount", "filled_size"):
        value = _decimal_float(payload.get(key))
        if value is not None:
            return value
    return None


def _venue_order_summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        "id": payload.get("id") or payload.get("orderID") or payload.get("order_id"),
        "status": payload.get("status") or payload.get("state"),
        "size_matched": payload.get("size_matched") or payload.get("matched_size"),
        "original_size": payload.get("original_size") or payload.get("size"),
        "price": payload.get("price"),
        "asset_id": payload.get("asset_id") or payload.get("token_id"),
        "market": payload.get("market"),
        "outcome": payload.get("outcome"),
    }


def _venue_payload_order_id(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    return str(payload.get("id") or payload.get("orderID") or payload.get("order_id") or "").strip()


def _find_open_order_payload(adapter: Any, venue_order_id: str) -> dict[str, Any] | None:
    get_open_orders = getattr(adapter, "get_open_orders", None)
    if not callable(get_open_orders):
        return None
    for raw in get_open_orders() or []:
        payload = _venue_payload(raw)
        if _venue_payload_order_id(payload).lower() == venue_order_id.lower():
            return payload
    return None


def _venue_point_order_boot_recoverable(item: dict[str, Any]) -> dict[str, Any] | None:
    risk = str(item.get("risk") or "")
    if risk == "venue_positive_match_not_projected_locally":
        return {
            **item,
            "repair_action": "edli_boot_command_recovery_live_tick_matched_order_facts",
            "repair_owner": "src.execution.command_recovery.reconcile_matched_order_facts",
        }
    if risk == "venue_terminal_match_not_projected_locally":
        return {
            **item,
            "repair_action": "edli_boot_command_recovery_live_tick_terminal_point_orders",
            "repair_owner": "src.execution.command_recovery.reconcile_terminal_point_orders",
        }
    if risk == "venue_terminal_no_fill_not_projected_locally":
        return {
            **item,
            "repair_action": "edli_boot_command_recovery_live_tick_terminal_no_fill",
            "repair_owner": "src.execution.command_recovery.reconcile_terminal_point_orders",
        }
    return None


def _venue_point_order_truth_alignment_check() -> CheckResult:
    """Classify authenticated venue point truth vs local restart order facts."""

    commands = _restart_relevant_entry_commands_for_venue_audit()
    evidence: dict[str, Any] = {
        "trade_db": str(TRADE_DB),
        "command_count": len(commands),
        "audit_limit": PREFLIGHT_VENUE_ORDER_AUDIT_LIMIT,
        "venue_read_timeout_seconds": PREFLIGHT_VENUE_READ_TIMEOUT_SECONDS,
    }
    if not commands:
        return CheckResult(
            "venue_point_order_truth_alignment",
            True,
            "no restart-relevant entry venue orders require point-order audit",
            evidence,
        )

    risky: list[dict[str, Any]] = []
    boot_recoverable: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    try:
        client, adapter = _preflight_venue_adapter()
    except Exception as exc:  # noqa: BLE001
        evidence["error"] = repr(exc)
        return CheckResult(
            "venue_point_order_truth_alignment",
            False,
            "authenticated venue point-order reader unavailable for restart-relevant orders",
            evidence,
        )
    try:
        for command in commands:
            venue_order_id = str(command.get("venue_order_id") or "").strip()
            command_id = str(command.get("command_id") or "").strip()
            try:
                payload = _venue_payload(adapter.get_order(venue_order_id))
            except Exception as exc:  # noqa: BLE001
                try:
                    payload = _find_open_order_payload(adapter, venue_order_id)
                except Exception as open_exc:  # noqa: BLE001
                    risky.append(
                        {
                            "command_id": command_id,
                            "venue_order_id": venue_order_id,
                            "risk": "venue_point_order_read_failed",
                            "point_error": repr(exc),
                            "open_orders_error": repr(open_exc),
                        }
                    )
                    continue
                if payload is None:
                    risky.append(
                        {
                            "command_id": command_id,
                            "venue_order_id": venue_order_id,
                            "risk": "venue_point_order_read_failed",
                            "point_error": repr(exc),
                            "open_orders_fallback_match": False,
                        }
                    )
                    continue
            if _venue_status(payload) in {"", "UNKNOWN"}:
                try:
                    fallback_payload = _find_open_order_payload(adapter, venue_order_id)
                except Exception as open_exc:  # noqa: BLE001
                    risky.append(
                        {
                            "command_id": command_id,
                            "venue_order_id": venue_order_id,
                            "risk": "venue_point_order_status_unknown",
                            "point_status": _venue_status(payload),
                            "open_orders_error": repr(open_exc),
                        }
                    )
                    continue
                if fallback_payload is not None:
                    payload = fallback_payload
            status = _venue_status(payload)
            venue_matched = _payload_matched_size(payload)
            local_matched = _decimal_float(command.get("latest_fact_matched_size")) or 0.0
            local_state = str(command.get("latest_fact_state") or "").strip().upper()
            item = {
                "command_id": command_id,
                "position_id": command.get("position_id"),
                "command_state": command.get("state"),
                "venue_order_id": venue_order_id,
                "venue_status": status,
                "venue_matched_size": venue_matched,
                "local_fact_state": local_state,
                "local_fact_matched_size": local_matched,
                "local_fact_remaining_size": command.get("latest_fact_remaining_size"),
                "local_fact_observed_at": command.get("latest_fact_observed_at"),
                "venue_order": _venue_order_summary(payload),
            }
            if payload is None:
                risky.append({**item, "risk": "venue_point_order_not_found"})
            elif status in {"", "UNKNOWN"}:
                risky.append({**item, "risk": "venue_point_order_status_unknown"})
            elif venue_matched is None and status in VENUE_POINT_MATCH_STATUSES:
                risky.append({**item, "risk": "venue_point_order_matched_size_missing"})
            else:
                risk = ""
                if (venue_matched or 0.0) > local_matched + 1e-9:
                    risk = "venue_positive_match_not_projected_locally"
                elif (
                    status in {"MATCHED", "FILLED", "MINED"}
                    and local_state not in {"MATCHED", "FILLED"}
                ):
                    risk = "venue_terminal_match_not_projected_locally"
                elif (
                    status in VENUE_POINT_TERMINAL_NO_FILL_STATUSES
                    and (venue_matched or 0.0) <= 1e-9
                    and local_state not in TERMINAL_VENUE_FACT_STATES
                ):
                    risk = "venue_terminal_no_fill_not_projected_locally"
                if risk:
                    risk_item = {**item, "risk": risk}
                    recoverable = _venue_point_order_boot_recoverable(risk_item)
                    if recoverable is not None:
                        boot_recoverable.append(recoverable)
                    else:
                        risky.append(risk_item)
                else:
                    covered.append(item)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    evidence["covered_count"] = len(covered)
    evidence["boot_recoverable"] = boot_recoverable
    evidence["risky"] = risky
    return CheckResult(
        "venue_point_order_truth_alignment",
        not risky,
        "authenticated venue point-order truth matches local restart-relevant order facts"
        if not risky and not boot_recoverable
        else "authenticated venue point-order drift is boot-recoverable before live order submission"
        if not risky
        else "authenticated venue point-order truth conflicts with local restart-relevant order facts",
        evidence,
    )


def _payload_matches_restart_relevant_entry_command(
    payload: dict[str, Any],
    command_index: dict[str, list[dict[str, Any]]],
) -> bool:
    event_id = str(payload.get("event_id") or "").strip()
    if not event_id:
        return False
    commands = command_index.get(event_id)
    if not commands:
        return False
    token_id = str(payload.get("token_id") or "").strip()
    if not token_id:
        return True
    return any(str(command.get("token_id") or "").strip() == token_id for command in commands)


def _live_money_certificate_parent_mode_check() -> CheckResult:
    """Block restart when a LIVE money-boundary certificate has non-LIVE parents."""

    evidence: dict[str, Any] = {
        "world_db": str(WORLD_DB),
        "lookback_hours": LIVE_ACTIONABLE_CERTIFICATE_LOOKBACK_HOURS,
        "certificate_types": list(LIVE_MONEY_CERTIFICATE_PARENT_MODE_TYPES),
    }
    if not WORLD_DB.exists():
        return CheckResult(
            "live_money_certificate_parent_modes",
            True,
            "world DB absent; no money-boundary certificate ancestry to inspect",
            evidence,
        )
    since = datetime.now(timezone.utc) - timedelta(
        hours=LIVE_ACTIONABLE_CERTIFICATE_LOOKBACK_HOURS
    )
    try:
        from src.state.decision_integrity_quarantine import (
            DECISION_CERTIFICATES_TABLE,
            REASON_INVALID_LIVE_PARENT_MODE,
        )
    except Exception as exc:  # noqa: BLE001
        evidence["error"] = str(exc)
        return CheckResult(
            "live_money_certificate_parent_modes",
            False,
            "could not load money certificate quarantine constants",
            evidence,
        )
    quarantined_hashes = _decision_certificate_quarantine_hashes(
        table_name=DECISION_CERTIFICATES_TABLE,
        reason_code=REASON_INVALID_LIVE_PARENT_MODE,
    )
    evidence["quarantined_count"] = len(quarantined_hashes)
    placeholders = ",".join("?" for _ in LIVE_MONEY_CERTIFICATE_PARENT_MODE_TYPES)
    params = (*LIVE_MONEY_CERTIFICATE_PARENT_MODE_TYPES, since.isoformat())
    try:
        conn = sqlite3.connect(f"file:{WORLD_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "main", "decision_certificates"):
            return CheckResult(
                "live_money_certificate_parent_modes",
                True,
                "decision_certificates table absent",
                evidence,
            )
        if not _table_exists(conn, "main", "decision_certificate_edges"):
            live_count = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                      FROM decision_certificates
                     WHERE certificate_type IN ({placeholders})
                       AND mode = 'LIVE'
                       AND verifier_status = 'VERIFIED'
                       AND datetime(decision_time) >= datetime(?)
                    """,
                    params,
                ).fetchone()[0]
                or 0
            )
            evidence["live_money_certificate_count"] = live_count
            return CheckResult(
                "live_money_certificate_parent_modes",
                live_count == 0,
                "decision_certificate_edges table absent and no recent live money certificates exist"
                if live_count == 0
                else "recent live money certificates exist but ancestry edge table is absent",
                evidence,
            )
        rows = conn.execute(
            f"""
            SELECT
                child.certificate_id AS child_certificate_id,
                child.certificate_hash AS child_certificate_hash,
                child.certificate_type AS child_certificate_type,
                child.decision_time AS child_decision_time,
                COUNT(*) AS bad_parent_count,
                GROUP_CONCAT(
                    edge.parent_role || '=' || edge.parent_certificate_type || ':' || COALESCE(parent.mode, 'MISSING'),
                    ','
                ) AS bad_parent_modes
              FROM decision_certificates child
              JOIN decision_certificate_edges edge
                ON edge.child_certificate_id = child.certificate_id
              LEFT JOIN decision_certificates parent
                ON parent.certificate_hash = edge.parent_certificate_hash
             WHERE child.certificate_type IN ({placeholders})
               AND child.mode = 'LIVE'
               AND child.verifier_status = 'VERIFIED'
               AND datetime(child.decision_time) >= datetime(?)
               AND COALESCE(parent.mode, '') != 'LIVE'
             GROUP BY
                child.certificate_id,
                child.certificate_hash,
                child.certificate_type,
                child.decision_time
             ORDER BY datetime(child.decision_time) DESC, child.certificate_id DESC
            """,
            params,
        ).fetchall()
    except sqlite3.Error as exc:
        evidence["error"] = str(exc)
        return CheckResult(
            "live_money_certificate_parent_modes",
            False,
            "could not inspect live money certificate ancestry",
            evidence,
        )
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass
    risky = [
        dict(row)
        for row in rows
        if str(row["child_certificate_hash"] or "") not in quarantined_hashes
    ]
    evidence["risky_count"] = len(risky)
    evidence["quarantined_risky_count"] = len(rows) - len(risky)
    evidence["risky"] = risky[:LIVE_ACTIONABLE_CERTIFICATE_SAMPLE_LIMIT]
    return CheckResult(
        "live_money_certificate_parent_modes",
        not risky,
        "recent live money-boundary certificates have LIVE parent ancestry"
        if not risky
        else "recent live money-boundary certificates include non-LIVE or missing parent ancestry",
        evidence,
    )


def _decision_certificate_quarantine_hashes(
    *,
    table_name: str,
    reason_code: str,
) -> set[str]:
    if not TRADE_DB.exists():
        return set()
    try:
        conn = sqlite3.connect(f"file:{TRADE_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "main", "decision_integrity_quarantine"):
            return set()
        rows = conn.execute(
            """
            SELECT row_id
              FROM decision_integrity_quarantine
             WHERE table_name = ?
               AND reason_code = ?
            """,
            (table_name, reason_code),
        ).fetchall()
        return {str(row["row_id"] or "") for row in rows if str(row["row_id"] or "")}
    except sqlite3.Error:
        return set()
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass


def _day0_canonical_observation_evidence(
    row: sqlite3.Row,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    city = str(row["city"] or "")
    target_date = str(row["target_date"] or "")
    metric = str(row["temperature_metric"] or "high").lower()
    if not city or not target_date or metric not in {"high", "low"}:
        return None
    try:
        from src.engine.monitor_refresh import _day0_observed_extreme_from_canonical_surface
    except Exception:
        return None
    try:
        conn = sqlite3.connect(f"file:{WORLD_DB}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        observed = _day0_observed_extreme_from_canonical_surface(
            city_name=city,
            target_date=target_date,
            metric_is_low=(metric == "low"),
            now=now,
            world_conn=conn,
        )
    except Exception:
        return None
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass
    if observed is None:
        return None
    extreme, observation_time, sample_count = observed
    return {
        "city": city,
        "target_date": target_date,
        "temperature_metric": metric,
        "observed_extreme": extreme,
        "observation_time": observation_time,
        "sample_count": sample_count,
        "source": "world.observation_instants",
    }


def _resting_venue_command_lifecycle_alignment_check() -> CheckResult:
    """Block restart when a live venue order is attached to the wrong lifecycle phase."""

    evidence: dict[str, Any] = {
        "trade_db": str(TRADE_DB),
        "terminal_command_states": sorted(TERMINAL_VENUE_COMMAND_STATES),
    }
    with _connect_live_ro() as conn:
        required_tables = ("venue_commands", "position_current")
        missing_tables = [
            table
            for table in required_tables
            if not _table_exists(conn, "main", table)
        ]
        if missing_tables:
            evidence["missing_tables"] = missing_tables
            return CheckResult(
                "resting_venue_command_lifecycle_alignment",
                True,
                "venue command tables absent; no resting venue command lifecycle alignment to inspect",
                evidence,
            )
        command_columns = _table_columns(conn, "main", "venue_commands")
        price_select = "cmd.price" if "price" in command_columns else "NULL"
        created_at_select = "cmd.created_at" if "created_at" in command_columns else "NULL"
        fact_join = ""
        fact_select = (
            "NULL AS latest_fact_state, NULL AS latest_fact_observed_at, "
            "NULL AS latest_fact_matched_size, NULL AS latest_fact_remaining_size, "
            "NULL AS latest_fact_raw_payload_json"
        )
        if _table_exists(conn, "main", "venue_order_facts"):
            fact_columns = _table_columns(conn, "main", "venue_order_facts")
            matched_select = (
                "vof.matched_size AS matched_size"
                if "matched_size" in fact_columns
                else "NULL AS matched_size"
            )
            remaining_select = (
                "vof.remaining_size AS remaining_size"
                if "remaining_size" in fact_columns
                else "NULL AS remaining_size"
            )
            raw_select = (
                "vof.raw_payload_json AS raw_payload_json"
                if "raw_payload_json" in fact_columns
                else "NULL AS raw_payload_json"
            )
            fact_select = (
                "lf.state AS latest_fact_state, lf.observed_at AS latest_fact_observed_at, "
                "lf.matched_size AS latest_fact_matched_size, "
                "lf.remaining_size AS latest_fact_remaining_size, "
                "lf.raw_payload_json AS latest_fact_raw_payload_json"
            )
            fact_join = """
              LEFT JOIN (
                SELECT command_id, state, observed_at, matched_size, remaining_size, raw_payload_json
                  FROM (
                    SELECT vof.command_id, vof.state, vof.observed_at,
                           {matched_select}, {remaining_select}, {raw_select},
                           ROW_NUMBER() OVER (
                               PARTITION BY vof.command_id
                               ORDER BY datetime(vof.observed_at) DESC, vof.rowid DESC
                           ) AS rn
                      FROM venue_order_facts vof
                  )
                 WHERE rn = 1
              ) lf
                ON lf.command_id = cmd.command_id
            """.format(
                matched_select=matched_select,
                remaining_select=remaining_select,
                raw_select=raw_select,
            )
        terminal_placeholders = ",".join("?" for _ in TERMINAL_VENUE_COMMAND_STATES)
        rows = conn.execute(
            f"""
            SELECT
                cmd.command_id,
                cmd.intent_kind,
                cmd.position_id,
                cmd.state AS command_state,
                cmd.venue_order_id,
                {price_select} AS price,
                cmd.size,
                {created_at_select} AS created_at,
                cmd.updated_at,
                pc.phase AS position_phase,
                pc.city,
                pc.target_date,
                pc.bin_label,
                pc.direction,
                pc.chain_shares,
                {fact_select}
              FROM venue_commands cmd
              LEFT JOIN position_current pc
                ON pc.position_id = cmd.position_id
              {fact_join}
             WHERE UPPER(COALESCE(cmd.state, '')) NOT IN ({terminal_placeholders})
               AND COALESCE(cmd.venue_order_id, '') != ''
             ORDER BY datetime(cmd.updated_at) DESC
             LIMIT 100
            """,
            tuple(sorted(TERMINAL_VENUE_COMMAND_STATES)),
        ).fetchall()
    risky: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    boot_recoverable: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        intent_kind = str(row["intent_kind"] or "").upper()
        phase = str(row["position_phase"] or "")
        fact_state = str(row["latest_fact_state"] or "").upper()
        risk = ""
        if fact_state in TERMINAL_VENUE_FACT_STATES and str(row["command_state"] or "").upper() not in TERMINAL_VENUE_COMMAND_STATES:
            risk = "command_projection_stale_after_terminal_venue_fact"
        elif intent_kind == "EXIT" and phase != "pending_exit":
            risk = "resting_exit_order_without_pending_exit_lifecycle"
        elif intent_kind == "ENTRY" and phase not in {"pending_entry", "active", "day0_window"}:
            risk = "resting_entry_order_without_entry_lifecycle"
        elif not phase:
            risk = "resting_order_missing_position_projection"
        if risk:
            recoverable = _resting_venue_command_boot_recoverable(item, risk)
            if recoverable is not None:
                boot_recoverable.append(recoverable)
            else:
                risky.append({**item, "risk": risk})
        else:
            covered.append(item)
    evidence["risky"] = risky
    evidence["boot_recoverable"] = boot_recoverable
    evidence["covered_count"] = len(covered)
    return CheckResult(
        "resting_venue_command_lifecycle_alignment",
        not risky,
        (
            "resting venue commands are aligned with position lifecycle"
            if not boot_recoverable
            else "resting venue command conflicts are boot-recoverable"
        )
        if not risky
        else "resting venue commands conflict with position lifecycle or terminal venue facts",
        evidence,
    )


def _positive_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed > 0.0:
        return parsed
    return None


def _payload_has_exit_fill_economics(raw: object, fallback_price: object) -> bool:
    try:
        payload = json.loads(str(raw or "{}"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    submit = payload.get("submit_result")
    if isinstance(submit, dict):
        making = _positive_float(submit.get("makingAmount") or submit.get("making_amount"))
        taking = _positive_float(submit.get("takingAmount") or submit.get("taking_amount"))
        if making is not None and taking is not None:
            return True
    return _positive_float(fallback_price) is not None


def _resting_venue_command_boot_recoverable(
    item: dict[str, Any],
    risk: str,
) -> dict[str, Any] | None:
    intent_kind = str(item.get("intent_kind") or "").upper()
    fact_state = str(item.get("latest_fact_state") or "").upper()
    phase = str(item.get("position_phase") or "")
    if (
        risk == "resting_exit_order_without_pending_exit_lifecycle"
        and intent_kind == "EXIT"
        and phase in {"active", "day0_window", "quarantined"}
        and fact_state in {"LIVE", "OPEN", "RESTING", "PARTIALLY_MATCHED", "PARTIAL"}
    ):
        return {
            **item,
            "risk": risk,
            "restart_resolution": "command_recovery.exit_lifecycle_alignment_repair",
            "repair_action": "restore_position_pending_exit_for_live_exit_order",
        }
    if (
        risk == "command_projection_stale_after_terminal_venue_fact"
        and intent_kind == "EXIT"
        and fact_state in {"MATCHED", "FILLED"}
        and _positive_float(item.get("latest_fact_matched_size")) is not None
        and _payload_has_exit_fill_economics(
            item.get("latest_fact_raw_payload_json"),
            item.get("price"),
        )
    ):
        return {
            **item,
            "risk": risk,
            "restart_resolution": "command_recovery.exit_lifecycle_alignment_repair",
            "repair_action": "terminalize_exit_command_and_project_economic_close",
        }
    return None


def _latest_iso_from_covered(rows: list[dict[str, Any]], key: str) -> str | None:
    latest: datetime | None = None
    for row in rows:
        dt = _parse_dt(row.get(key))
        if dt is None:
            continue
        if latest is None or dt > latest:
            latest = dt
    return latest.isoformat() if latest is not None else None


def _execution_feasibility_age_is_fresh(age_seconds: float) -> bool:
    return (
        -EXECUTION_FEASIBILITY_CLOCK_SKEW_TOLERANCE_SECONDS
        <= age_seconds
        <= EXECUTION_FEASIBILITY_MAX_AGE_SECONDS
    )


def _execution_feasibility_evidence_check(rows: list[sqlite3.Row]) -> CheckResult:
    now = datetime.now(timezone.utc)
    evidence: dict[str, Any] = {
        "table": "execution_feasibility_evidence",
        "max_age_seconds": EXECUTION_FEASIBILITY_MAX_AGE_SECONDS,
    }
    if not rows:
        evidence["scoped_exposure_count"] = 0
        evidence["row_count"] = "not_scanned_no_open_exposures"
        return CheckResult(
            "execution_feasibility_evidence_freshness",
            True,
            "no open exposures require execution feasibility evidence",
            evidence,
        )
    canonical_covered: list[dict[str, Any]] = []
    quote_required_rows: list[sqlite3.Row] = []
    for row in rows:
        canonical = _day0_canonical_observation_evidence(row, now=now)
        if canonical is not None:
            canonical_covered.append(
                {
                    **_exposure_stub(_open_exposure_identity(row)),
                    "freshness_basis": "day0_canonical_observation_no_execution_quote_required",
                    "restart_resolution": "boot_monitor_refresh_from_canonical_day0_observation",
                    "canonical_observation": canonical,
                }
            )
        else:
            quote_required_rows.append(row)
    if not quote_required_rows:
        evidence["row_count"] = "not_scanned_no_quote_required_after_canonical_day0"
        evidence["scoped_exposure_count"] = len(rows)
        evidence["risky"] = []
        evidence["covered"] = canonical_covered
        evidence["latest_observed_at"] = None
        evidence["latest_quote_seen_at"] = None
        return CheckResult(
            "execution_feasibility_evidence_freshness",
            True,
            "all open exposures are covered by canonical Day0 observations",
            evidence,
        )
    with _connect_live_ro() as conn:
        if not _table_exists(conn, "main", "execution_feasibility_evidence"):
            return CheckResult(
                "execution_feasibility_evidence_freshness",
                False,
                "execution feasibility evidence table is missing",
                evidence,
            )
        columns = _table_columns(conn, "main", "execution_feasibility_evidence")
        exposure_results = _execution_feasibility_exposure_freshness(
            conn,
            columns=columns,
            exposures=[_open_exposure_identity(row) for row in quote_required_rows],
            now=now,
        )
    exposure_results["covered"] = canonical_covered + list(exposure_results["covered"])
    exposure_results["scoped_exposure_count"] = len(rows)
    latest = _latest_iso_from_covered(exposure_results["covered"], "latest_observed_at")
    latest_dt = _parse_dt(latest)
    evidence["row_count"] = "not_scanned_append_only_hot_path"
    evidence["latest_observed_at"] = latest
    evidence["latest_quote_seen_at"] = _latest_iso_from_covered(
        exposure_results["covered"], "latest_quote_seen_at"
    )
    evidence["clock_skew_tolerance_seconds"] = (
        EXECUTION_FEASIBILITY_CLOCK_SKEW_TOLERANCE_SECONDS
    )
    evidence.update(exposure_results)
    if latest_dt is None:
        return CheckResult(
            "execution_feasibility_evidence_freshness",
            False,
            "execution feasibility evidence is absent or timestamp-invalid",
            evidence,
        )
    age = (now - latest_dt).total_seconds()
    evidence["age_seconds"] = age
    if age < 0:
        evidence["clock_skew_tolerated_seconds"] = min(
            abs(age), EXECUTION_FEASIBILITY_CLOCK_SKEW_TOLERANCE_SECONDS
        )
    ok = _execution_feasibility_age_is_fresh(age) and not exposure_results["risky"]
    return CheckResult(
        "execution_feasibility_evidence_freshness",
        ok,
        (
            "execution feasibility evidence is fresh for open exposures"
            if ok
            else "execution feasibility evidence is stale/missing for open exposures"
        ),
        evidence,
    )


def _execution_feasibility_exposure_freshness(
    conn: sqlite3.Connection,
    *,
    columns: set[str],
    exposures: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    risky: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    for exposure in exposures:
        predicate = _freshness_predicate_for_exposure(
            columns=columns,
            exposure=exposure,
            token_columns=("token_id",),
            include_condition_id=False,
        )
        item = _exposure_stub(exposure)
        if predicate is None:
            risky.append({**item, "risk": "missing_execution_identity_for_feasibility"})
            continue
        where_sql, params = predicate
        observed_expr = "created_at" if "created_at" in columns else "quote_seen_at"
        row = conn.execute(
            f"""
            SELECT {observed_expr} AS latest_observed_at,
                   quote_seen_at AS latest_quote_seen_at
              FROM execution_feasibility_evidence
             WHERE {where_sql}
             ORDER BY {observed_expr} DESC
             LIMIT 1
            """,
            params,
        ).fetchone()
        latest_observed = row["latest_observed_at"] if row else None
        latest_quote = row["latest_quote_seen_at"] if row else None
        latest_dt = _parse_dt(latest_observed)
        evidence = {
            **item,
            "latest_observed_at": latest_observed,
            "latest_quote_seen_at": latest_quote,
            "freshness_basis": observed_expr,
        }
        if latest_dt is None:
            risky.append({**evidence, "risk": "missing_execution_feasibility_evidence"})
            continue
        age = (now - latest_dt).total_seconds()
        evidence["age_seconds"] = age
        if age < 0:
            evidence["clock_skew_tolerated_seconds"] = min(
                abs(age), EXECUTION_FEASIBILITY_CLOCK_SKEW_TOLERANCE_SECONDS
            )
        covered.append(evidence)
        if not _execution_feasibility_age_is_fresh(age):
            risk = (
                "future_execution_feasibility_evidence"
                if age < 0
                else "stale_execution_feasibility_evidence"
            )
            risky.append({**evidence, "risk": risk})
    return {"scoped_exposure_count": len(exposures), "risky": risky, "covered": covered}


def _full_family_executable_substrate_redecision_check(rows: list[sqlite3.Row]) -> CheckResult:
    now = datetime.now(timezone.utc)
    evidence: dict[str, Any] = {
        "table": "executable_market_snapshots",
        "max_age_seconds": EXECUTABLE_SUBSTRATE_MAX_AGE_SECONDS,
        "restart_blocking": False,
        "price_exit_authority": "execution_feasibility_evidence",
        "scope": "full_family_redecision_shift_fillup",
    }
    if not rows:
        evidence["scoped_exposure_count"] = 0
        evidence["row_count"] = "not_scanned_no_open_exposures"
        return CheckResult(
            "full_family_executable_substrate_redecision_coverage",
            True,
            "no open exposures require full-family executable substrate",
            evidence,
        )
    with _connect_live_ro() as conn:
        if not _table_exists(conn, "main", "executable_market_snapshots"):
            return CheckResult(
                "full_family_executable_substrate_redecision_coverage",
                False,
                "executable market snapshot table is missing",
                evidence,
            )
        columns = _table_columns(conn, "main", "executable_market_snapshots")
        exposure_results = _executable_substrate_exposure_freshness(
            conn,
            columns=columns,
            exposures=[_open_exposure_identity(row) for row in rows],
            now=now,
        )
    latest_captured = _latest_iso_from_covered(exposure_results["covered"], "latest_captured_at")
    latest_deadline = _latest_iso_from_covered(exposure_results["covered"], "latest_freshness_deadline")
    captured_dt = _parse_dt(latest_captured)
    deadline_dt = _parse_dt(latest_deadline)
    evidence["row_count"] = "not_scanned_append_only_hot_path"
    evidence["latest_captured_at"] = latest_captured
    evidence["latest_freshness_deadline"] = latest_deadline
    evidence.update(exposure_results)
    if captured_dt is None:
        return CheckResult(
            "executable_substrate_freshness",
            False,
            "executable market substrate is absent or timestamp-invalid",
            evidence,
        )
    age = (now - captured_dt).total_seconds()
    deadline_ok = deadline_dt is not None and deadline_dt >= now
    age_ok = 0.0 <= age <= EXECUTABLE_SUBSTRATE_MAX_AGE_SECONDS
    evidence["age_seconds"] = age
    evidence["freshness_deadline_ok"] = deadline_ok
    ok = True
    return CheckResult(
        "full_family_executable_substrate_redecision_coverage",
        ok,
        (
            "full-family executable substrate table exists; stale rows are reported as redecision coverage risk, not restart exit blocker"
            if ok
            else "full-family executable substrate table is missing"
        ),
        evidence,
    )


def _executable_substrate_exposure_freshness(
    conn: sqlite3.Connection,
    *,
    columns: set[str],
    exposures: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    risky: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    for exposure in exposures:
        predicate = _freshness_predicate_for_exposure(
            columns=columns,
            exposure=exposure,
            token_columns=("token_id", "yes_token_id", "no_token_id", "selected_outcome_token_id"),
        )
        item = _exposure_stub(exposure)
        if predicate is None:
            risky.append({**item, "risk": "missing_execution_identity_for_substrate"})
            continue
        where_sql, params = predicate
        row = conn.execute(
            f"""
            SELECT captured_at AS latest_captured_at,
                   freshness_deadline AS latest_freshness_deadline
              FROM executable_market_snapshots
             WHERE {where_sql}
             ORDER BY captured_at DESC
             LIMIT 1
            """,
            params,
        ).fetchone()
        captured_dt = _parse_dt(row["latest_captured_at"] if row else None)
        deadline_dt = _parse_dt(row["latest_freshness_deadline"] if row else None)
        evidence = {
            **item,
            "latest_captured_at": row["latest_captured_at"] if row else None,
            "latest_freshness_deadline": row["latest_freshness_deadline"] if row else None,
        }
        if captured_dt is None:
            risky.append({**evidence, "risk": "missing_executable_substrate"})
            continue
        age = (now - captured_dt).total_seconds()
        deadline_ok = deadline_dt is not None and deadline_dt >= now
        evidence["age_seconds"] = age
        evidence["freshness_deadline_ok"] = deadline_ok
        covered.append(evidence)
        if not (0.0 <= age <= EXECUTABLE_SUBSTRATE_MAX_AGE_SECONDS or deadline_ok):
            risky.append({**evidence, "risk": "stale_executable_substrate"})
    return {"scoped_exposure_count": len(exposures), "risky": risky, "covered": covered}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _harvester_live_enabled() -> tuple[bool, dict[str, Any]]:
    """Return whether the restart target will run the settlement P&L resolver.

    The preflight usually runs from an operator shell, not inside launchd. The
    shell environment is therefore not restart-target evidence and must not
    override the active live-trading LaunchAgent.
    """
    env_value = os.environ.get("ZEUS_HARVESTER_LIVE_ENABLED")
    evidence: dict[str, Any] = {
        "shell_env_value_ignored": env_value,
        "plist_path": str(LIVE_TRADING_PLIST_PATH),
        "plist_value": None,
        "source": "live_trading_launchagent_plist",
    }

    try:
        with LIVE_TRADING_PLIST_PATH.open("rb") as handle:
            payload = plistlib.load(handle)
    except Exception as exc:
        evidence["plist_error"] = str(exc)
        return False, evidence
    env_vars = payload.get("EnvironmentVariables")
    plist_value = None
    if isinstance(env_vars, dict):
        plist_value = env_vars.get("ZEUS_HARVESTER_LIVE_ENABLED")
    evidence["plist_value"] = plist_value
    return str(plist_value or "") == "1", evidence


def _forecast_sidecar_health() -> CheckResult:
    now = datetime.now(timezone.utc)
    current_git_head = _git_head()
    heartbeat = _read_json(FORECAST_LIVE_HEARTBEAT_PATH)
    heartbeat_at = _parse_dt(heartbeat.get("written_at") or heartbeat.get("timestamp"))
    heartbeat_age = None
    if heartbeat_at is not None:
        heartbeat_age = (now - heartbeat_at).total_seconds()

    scheduler_health = _read_json(SCHEDULER_HEALTH_PATH)
    job_evidence: dict[str, Any] = {}
    risky: list[dict[str, Any]] = []
    for job_name in REPLACEMENT_SCHEDULER_HEALTH_JOBS:
        entry = scheduler_health.get(job_name)
        if not isinstance(entry, dict):
            risky.append({"job": job_name, "risk": "missing_scheduler_health_entry"})
            job_evidence[job_name] = None
            continue
        status = str(entry.get("status") or "")
        last_success = _parse_dt(entry.get("last_success_at"))
        last_failure = _parse_dt(entry.get("last_failure_at"))
        last_started = _parse_dt(entry.get("last_started_at") or entry.get("last_run_at"))
        running_age = None
        if last_started is not None:
            running_age = (now - last_started).total_seconds()
        business_liveness = entry.get("business_liveness")
        if not isinstance(business_liveness, dict):
            business_liveness = {}
        item = {
            "status": status,
            "last_run_at": entry.get("last_run_at"),
            "last_started_at": entry.get("last_started_at"),
            "last_success_at": entry.get("last_success_at"),
            "last_failure_at": entry.get("last_failure_at"),
            "last_failure_reason": entry.get("last_failure_reason"),
            "last_skip_at": entry.get("last_skip_at"),
            "last_skip_reason": entry.get("last_skip_reason"),
            "business_liveness": business_liveness,
            "running_age_seconds": running_age,
        }
        job_evidence[job_name] = item
        if status == "FAILED":
            risky.append({"job": job_name, "risk": "scheduler_job_failed", **item})
            continue
        if status == "RUNNING":
            if last_started is None:
                risky.append({"job": job_name, "risk": "scheduler_job_running_start_missing", **item})
            elif running_age is None or running_age < 0.0:
                risky.append({"job": job_name, "risk": "scheduler_job_running_clock_invalid", **item})
            elif running_age > REPLACEMENT_SIDECAR_RUNNING_MAX_AGE_SECONDS:
                risky.append({"job": job_name, "risk": "scheduler_job_running_stale", **item})
            continue
        if (
            job_name == "bayes_precision_fusion_capture"
            and status == "SKIPPED"
            and business_liveness.get("transport_degraded") is True
        ):
            continue
        if status != "OK":
            risky.append({"job": job_name, "risk": "scheduler_job_not_ok", **item})
            continue
        if last_failure is not None and (last_success is None or last_failure > last_success):
            risky.append({"job": job_name, "risk": "latest_scheduler_outcome_failed", **item})

    heartbeat_ok = (
        str(heartbeat.get("daemon") or "") == "forecast-live"
        and heartbeat_age is not None
        and 0.0 <= heartbeat_age <= FORECAST_LIVE_HEARTBEAT_MAX_AGE_SECONDS
    )
    if not heartbeat_ok:
        risky.append(
            {
                "job": "forecast-live-heartbeat",
                "risk": "forecast_live_heartbeat_stale_or_missing",
                "heartbeat_age_seconds": heartbeat_age,
                "heartbeat": heartbeat,
            }
        )
    if str(heartbeat.get("git_head") or "") != current_git_head:
        risky.append(
            {
                "job": "forecast-live-heartbeat",
                "risk": "forecast_live_code_head_mismatch",
                "heartbeat_git_head": heartbeat.get("git_head"),
                "current_git_head": current_git_head,
            }
        )
    heartbeat_jobs_raw = heartbeat.get("jobs")
    heartbeat_jobs = set(heartbeat_jobs_raw) if isinstance(heartbeat_jobs_raw, list) else set()
    missing_heartbeat_jobs = sorted(set(REPLACEMENT_HEARTBEAT_JOBS) - heartbeat_jobs)
    if missing_heartbeat_jobs:
        risky.append(
            {
                "job": "forecast-live-heartbeat",
                "risk": "forecast_live_heartbeat_missing_replacement_jobs",
                "missing_jobs": missing_heartbeat_jobs,
                "heartbeat_jobs": sorted(heartbeat_jobs),
            }
        )

    ok = not risky
    return CheckResult(
        "forecast_sidecar_health",
        ok,
        "forecast sidecar heartbeat and replacement jobs are healthy"
        if ok
        else "forecast sidecar heartbeat or replacement production jobs are unhealthy",
        {
            "heartbeat_path": str(FORECAST_LIVE_HEARTBEAT_PATH),
            "heartbeat_age_seconds": heartbeat_age,
            "heartbeat": heartbeat,
            "current_git_head": current_git_head,
            "scheduler_health_path": str(SCHEDULER_HEALTH_PATH),
            "jobs": job_evidence,
            "risky": risky,
            "heartbeat_max_age_seconds": FORECAST_LIVE_HEARTBEAT_MAX_AGE_SECONDS,
            "replacement_sidecar_running_max_age_seconds": (
                REPLACEMENT_SIDECAR_RUNNING_MAX_AGE_SECONDS
            ),
        },
    )


def _posterior_summary() -> CheckResult:
    now = datetime.now(timezone.utc)
    with _connect_live_ro() as conn:
        runtime_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT COALESCE(runtime_layer, '') AS runtime_layer,
                       COUNT(*) AS rows,
                       MIN(computed_at) AS min_computed_at,
                       MAX(computed_at) AS max_computed_at
                  FROM forecasts.forecast_posteriors
                 GROUP BY COALESCE(runtime_layer, '')
                 ORDER BY runtime_layer
                """
            )
        ]
        latest = conn.execute(
            """
            SELECT computed_at, source_cycle_time
              FROM forecasts.forecast_posteriors
             WHERE runtime_layer = 'live'
             ORDER BY datetime(computed_at) DESC, posterior_id DESC
             LIMIT 1
            """
        ).fetchone()
    latest_dt = _parse_dt(latest["computed_at"]) if latest else None
    source_cycle_dt = _parse_dt(latest["source_cycle_time"]) if latest else None
    age_hours = None
    if latest_dt is not None:
        age_hours = (now - latest_dt).total_seconds() / 3600.0
    source_cycle_age_hours = None
    source_cycle_fresh = False
    if source_cycle_dt is not None:
        try:
            from src.data.replacement_forecast_cycle_policy import (
                cycle_age_exceeds_bound,
                cycle_age_hours,
                replacement_source_cycle_max_age_hours,
            )

            source_cycle_age_hours = cycle_age_hours(now, source_cycle_dt)
            max_age_hours = replacement_source_cycle_max_age_hours()
            source_cycle_fresh = (
                0.0 <= source_cycle_age_hours
                and not cycle_age_exceeds_bound(now, source_cycle_dt, max_age_hours=max_age_hours)
            )
        except Exception:
            source_cycle_age_hours = (now - source_cycle_dt).total_seconds() / 3600.0
            source_cycle_fresh = False
            from src.engine.position_belief import monitor_belief_max_age_hours

            max_age_hours = monitor_belief_max_age_hours()
    else:
        from src.engine.position_belief import monitor_belief_max_age_hours

        max_age_hours = monitor_belief_max_age_hours()
    non_live = sum(int(row["rows"]) for row in runtime_rows if row["runtime_layer"] != "live")
    ok = non_live == 0 and source_cycle_fresh
    return CheckResult(
        "live_posterior_freshness",
        ok,
        "latest live posterior is fresh" if ok else "latest live posterior is stale/missing or non-live rows exist",
        {
            "runtime_layers": runtime_rows,
            "latest_live_computed_at": latest["computed_at"] if latest else None,
            "latest_live_age_hours": age_hours,
            "latest_live_source_cycle_time": latest["source_cycle_time"] if latest else None,
            "latest_live_source_cycle_age_hours": source_cycle_age_hours,
            "non_live_rows": non_live,
            "freshness_basis": "source_cycle_time" if source_cycle_dt is not None else "computed_at",
            "fresh_age_limit_hours": max_age_hours,
        },
    )


def _open_positions(*, positive_chain_only: bool = True) -> list[Any]:
    with _connect_live_ro() as conn:
        columns = _table_columns(conn, "main", "position_current")
        optional_selects = []
        for column in (
            "condition_id",
            "token_id",
            "no_token_id",
            "entry_method",
            "chain_state",
            "p_posterior",
            "cost_basis_usd",
        ):
            optional_selects.append(column if column in columns else f"NULL AS {column}")
        chain_filter = "AND COALESCE(chain_shares, shares, 0) > 0" if positive_chain_only else ""
        return list(
            conn.execute(
                f"""
                SELECT position_id, phase, city, target_date, temperature_metric,
                       bin_label, direction, shares, chain_shares, order_status,
                       exit_reason, exit_retry_count, next_exit_retry_at,
                       last_monitor_prob, last_monitor_prob_is_fresh,
                       last_monitor_market_price, last_monitor_market_price_is_fresh,
                       updated_at,
                       {", ".join(optional_selects)}
                  FROM position_current
                 WHERE phase IN ('active', 'day0_window', 'pending_exit')
                   {chain_filter}
                 ORDER BY CASE phase WHEN 'pending_exit' THEN 0 ELSE 1 END,
                          city, target_date, bin_label
                """
            )
        )


def _position_current_projection_integrity_check(rows: list[sqlite3.Row]) -> CheckResult:
    """Block restart when canonical live positions contradict terminal or EDLI authority."""

    evidence: dict[str, Any] = {
        "trade_db": str(TRADE_DB),
        "hard_terminal_phases": sorted(HARD_TERMINAL_POSITION_PHASES),
        "edli_entry_method_required": "qkernel_spine",
    }
    if not rows:
        return CheckResult(
            "position_current_projection_integrity",
            True,
            "no open positive-share positions require projection integrity checks",
            evidence,
        )

    position_ids = [
        str(row["position_id"] or "")
        for row in rows
        if str(row["position_id"] or "").strip()
    ]
    terminal_by_position: dict[str, dict[str, Any]] = {}
    latest_entry_by_position: dict[str, dict[str, Any]] = {}
    with _connect_live_ro() as conn:
        if position_ids and _table_exists(conn, "main", "position_events"):
            event_columns = _table_columns(conn, "main", "position_events")
            event_conditions: list[str] = []
            event_params: list[str] = []
            if "phase_after" in event_columns:
                phase_placeholders = ",".join("?" for _ in HARD_TERMINAL_POSITION_PHASES)
                event_conditions.append(f"LOWER(COALESCE(phase_after, '')) IN ({phase_placeholders})")
                event_params.extend(sorted(HARD_TERMINAL_POSITION_PHASES))
            if "event_type" in event_columns:
                event_placeholders = ",".join("?" for _ in HARD_TERMINAL_POSITION_EVENT_TYPES)
                event_conditions.append(f"UPPER(COALESCE(event_type, '')) IN ({event_placeholders})")
                event_params.extend(sorted(HARD_TERMINAL_POSITION_EVENT_TYPES))
            if not event_conditions:
                event_rows = []
            else:
                phase_before_select = (
                    "phase_before" if "phase_before" in event_columns else "NULL AS phase_before"
                )
                phase_after_select = (
                    "phase_after" if "phase_after" in event_columns else "NULL AS phase_after"
                )
                payload_select = (
                    "payload_json" if "payload_json" in event_columns else "NULL AS payload_json"
                )
                event_type_select = (
                    "event_type" if "event_type" in event_columns else "NULL AS event_type"
                )
                occurred_at_select = (
                    "occurred_at" if "occurred_at" in event_columns else "NULL AS occurred_at"
                )
                sequence_select = (
                    "sequence_no" if "sequence_no" in event_columns else "0 AS sequence_no"
                )
                placeholders = ",".join("?" for _ in position_ids)
                event_rows = conn.execute(
                    f"""
                    WITH terminal_events AS (
                        SELECT
                            position_id,
                            {event_type_select},
                            {phase_before_select},
                            {phase_after_select},
                            {sequence_select},
                            {occurred_at_select},
                            {payload_select},
                            ROW_NUMBER() OVER (
                                PARTITION BY position_id
                                ORDER BY sequence_no DESC, datetime(occurred_at) DESC
                            ) AS rn
                          FROM position_events
                         WHERE position_id IN ({placeholders})
                           AND ({" OR ".join(event_conditions)})
                    )
                    SELECT position_id, event_type, phase_before, phase_after,
                           sequence_no, occurred_at, payload_json
                      FROM terminal_events
                     WHERE rn = 1
                    """,
                    tuple(position_ids) + tuple(event_params),
                ).fetchall()
            terminal_by_position = {
                str(row["position_id"]): dict(row)
                for row in event_rows
            }

        if position_ids and _table_exists(conn, "main", "venue_commands"):
            placeholders = ",".join("?" for _ in position_ids)
            command_columns = _table_columns(conn, "main", "venue_commands")
            if "intent_kind" not in command_columns or "position_id" not in command_columns:
                command_rows = []
            else:
                decision_select = (
                    "decision_id" if "decision_id" in command_columns else "NULL AS decision_id"
                )
                state_select = "state" if "state" in command_columns else "NULL AS state"
                venue_order_select = (
                    "venue_order_id" if "venue_order_id" in command_columns else "NULL AS venue_order_id"
                )
                size_select = "size" if "size" in command_columns else "NULL AS size"
                price_select = "price" if "price" in command_columns else "NULL AS price"
                created_select = (
                    "created_at" if "created_at" in command_columns else "updated_at AS created_at"
                )
                command_order_time = (
                    "COALESCE(created_at, updated_at)"
                    if "created_at" in command_columns
                    else "updated_at"
                )
                command_rows = conn.execute(
                    f"""
                    WITH latest_entry AS (
                        SELECT
                            position_id,
                            command_id,
                            {decision_select},
                            {state_select},
                            {venue_order_select},
                            {price_select},
                            {size_select},
                            {created_select},
                            updated_at,
                            ROW_NUMBER() OVER (
                                PARTITION BY position_id
                                ORDER BY datetime({command_order_time}) DESC,
                                         command_id DESC
                            ) AS rn
                          FROM venue_commands
                         WHERE intent_kind = 'ENTRY'
                           AND position_id IN ({placeholders})
                    )
                    SELECT position_id, command_id, decision_id, state, venue_order_id,
                           price, size, created_at, updated_at
                      FROM latest_entry
                     WHERE rn = 1
                    """,
                    tuple(position_ids),
                ).fetchall()
            latest_entry_by_position = {
                str(row["position_id"]): dict(row)
                for row in command_rows
            }

    risky: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    for row in rows:
        position_id = str(row["position_id"] or "")
        item = {
            "position_id": position_id,
            "phase": row["phase"],
            "city": row["city"],
            "target_date": row["target_date"],
            "temperature_metric": row["temperature_metric"],
            "bin_label": row["bin_label"],
            "direction": row["direction"],
            "shares": row["shares"],
            "chain_shares": row["chain_shares"],
            "entry_method": row["entry_method"],
            "p_posterior": row["p_posterior"],
            "cost_basis_usd": row["cost_basis_usd"],
        }
        terminal = terminal_by_position.get(position_id)
        if terminal is not None:
            risky.append(
                {
                    **item,
                    "risk": "open_position_after_hard_terminal_event",
                    "terminal_event": terminal,
                }
            )
            continue

        latest_entry = latest_entry_by_position.get(position_id)
        decision_id = str((latest_entry or {}).get("decision_id") or "")
        entry_method = str(row["entry_method"] or "")
        try:
            p_posterior = float(row["p_posterior"])
        except (TypeError, ValueError):
            p_posterior = None
        if decision_id.startswith("edli_exec_cmd:") and entry_method != "qkernel_spine":
            risky.append(
                {
                    **item,
                    "risk": "edli_entry_projected_without_qkernel_authority",
                    "entry_command": latest_entry,
                    "required_entry_method": "qkernel_spine",
                }
            )
            continue
        if decision_id.startswith("edli_exec_cmd:") and (
            p_posterior is None or p_posterior <= 0.0
        ):
            risky.append(
                {
                    **item,
                    "risk": "edli_entry_zero_probability_projection",
                    "entry_command": latest_entry,
                }
            )
            continue
        covered.append(item)

    evidence["risky"] = risky
    evidence["covered_count"] = len(covered)
    return CheckResult(
        "position_current_projection_integrity",
        not risky,
        "open position projections align with terminal and EDLI authority"
        if not risky
        else "open position projections contradict terminal events or EDLI authority",
        evidence,
    )


def _requires_executable_quote(row: sqlite3.Row, *, now_utc: datetime) -> bool:
    """Whether restart preflight should require fresh executable book evidence.

    A venue-closed position cannot be acted through CLOB anymore regardless of
    its local lifecycle phase. It must be handled by settlement/harvester
    recovery and the pending-exit/held-belief checks, not by waiting forever for
    fresh executable substrate or quote evidence.
    """

    try:
        from src.strategy.market_phase import family_venue_closed

        if family_venue_closed(
            city=str(row["city"] or ""),
            target_date=str(row["target_date"] or ""),
            now_utc=now_utc,
        ):
            return False
    except Exception:
        return True
    return True


def _open_positions_requiring_executable_quote(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    now = datetime.now(timezone.utc)
    return [row for row in rows if _requires_executable_quote(row, now_utc=now)]


def _verified_settlement_truth_for(rows: list[sqlite3.Row]) -> dict[tuple[str, str, str], dict[str, Any]]:
    keys: set[tuple[str, str, str]] = set()
    for row in rows:
        if row["phase"] not in {"active", "day0_window"}:
            continue
        city = str(row["city"] or "").strip()
        target_date = str(row["target_date"] or "").strip()
        metric = str(row["temperature_metric"] or "high").strip().lower()
        if city and target_date and metric in {"high", "low"}:
            keys.add((city, target_date, metric))
    if not keys:
        return {}

    truth: dict[tuple[str, str, str], dict[str, Any]] = {}
    key_list = sorted(keys)
    with _connect_live_ro() as conn:
        for offset in range(0, len(key_list), 250):
            batch = key_list[offset: offset + 250]
            placeholders = ",".join(["(?, ?, ?)"] * len(batch))
            params: list[str] = []
            for city, target_date, metric in batch:
                params.extend([city, target_date, metric])
            try:
                fetched = conn.execute(
                    f"""
                    SELECT city, target_date, COALESCE(temperature_metric, 'high') AS temperature_metric,
                           market_slug, winning_bin, authority, settlement_source,
                           settlement_value, settled_at
                      FROM forecasts.settlement_outcomes
                     WHERE authority = 'VERIFIED'
                       AND (city, target_date, COALESCE(temperature_metric, 'high')) IN ({placeholders})
                     ORDER BY datetime(settled_at) DESC
                    """,
                    params,
                ).fetchall()
            except sqlite3.OperationalError:
                return truth
            for row in fetched:
                key = (
                    str(row["city"] or ""),
                    str(row["target_date"] or ""),
                    str(row["temperature_metric"] or "high").lower(),
                )
                truth.setdefault(
                    key,
                    {
                        "market_slug": row["market_slug"],
                        "winning_bin": row["winning_bin"],
                        "authority": row["authority"],
                        "settlement_source": row["settlement_source"],
                        "settlement_value": row["settlement_value"],
                        "settled_at": row["settled_at"],
                    },
                )
    return truth


def _pending_exit_check(rows: list[sqlite3.Row]) -> CheckResult:
    risky: list[dict[str, Any]] = []
    tolerated: list[dict[str, Any]] = []
    full_fill_repairable = _exit_full_fill_repairable_by_position()
    retry_resumable = _exit_retry_resumable_by_position()
    for row in rows:
        if row["phase"] != "pending_exit":
            continue
        shares = float(row["chain_shares"] if row["chain_shares"] is not None else row["shares"] or 0.0)
        reason = str(row["exit_reason"] or "")
        order_status = str(row["order_status"] or "")
        item = {
            "position_id": row["position_id"],
            "city": row["city"],
            "target_date": row["target_date"],
            "bin_label": row["bin_label"],
            "shares": shares,
            "order_status": order_status,
            "exit_reason": reason,
        }
        if reason == "MARKET_CLOSED_AWAITING_SETTLEMENT":
            tolerated.append(item)
        elif str(row["position_id"] or "") in full_fill_repairable:
            item = {
                **item,
                "restart_resolution": "command_recovery_full_exit_fill_close",
                "repair_evidence": full_fill_repairable[str(row["position_id"] or "")],
            }
            tolerated.append(item)
        elif str(row["position_id"] or "") in retry_resumable:
            _retry_evidence = retry_resumable[str(row["position_id"] or "")]
            item = {
                **item,
                "restart_resolution": _retry_evidence.get(
                    "restart_resolution", "exit_lifecycle_retry_resume"
                ),
                "repair_evidence": _retry_evidence,
            }
            tolerated.append(item)
        elif reason == "EXIT_CHAIN_DUST_STILL_HELD" and shares <= DUST_SHARE_LIMIT:
            tolerated.append(item)
            if order_status != "backoff_exhausted":
                item = dict(item)
                item["risk"] = "dust_projection_needs_backoff_exhausted_reload_repair"
                risky.append(item)
        else:
            risky.append(item)
    return CheckResult(
        "pending_exit_restart_risk",
        not risky,
        "no restart-dangerous pending exits" if not risky else "pending exits need resolution before armed restart",
        {"risky": risky, "tolerated": tolerated},
    )


def _exit_full_fill_repairable_by_position() -> dict[str, dict[str, Any]]:
    with _connect_live_ro() as conn:
        if not (
            _table_exists(conn, "main", "venue_commands")
            and _table_exists(conn, "main", "venue_trade_facts")
            and _table_exists(conn, "main", "position_current")
        ):
            return {}
        rows = conn.execute(
            """
            SELECT pc.position_id,
                   cmd.command_id,
                   cmd.venue_order_id,
                   cmd.size AS command_size,
                   COALESCE(pc.chain_shares, pc.shares, 0) AS position_shares,
                   SUM(CAST(COALESCE(tf.filled_size, '0') AS REAL)) AS filled_size,
                   SUM(CAST(COALESCE(tf.filled_size, '0') AS REAL)
                       * CAST(COALESCE(tf.fill_price, '0') AS REAL)) AS fill_notional,
                   GROUP_CONCAT(DISTINCT tf.state) AS trade_states,
                   MAX(tf.observed_at) AS observed_at
              FROM position_current pc
              JOIN venue_commands cmd
                ON cmd.position_id = pc.position_id
               AND cmd.intent_kind = 'EXIT'
              JOIN venue_trade_facts tf
                ON tf.command_id = cmd.command_id
               AND tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
             WHERE pc.phase = 'pending_exit'
             GROUP BY pc.position_id, cmd.command_id, cmd.venue_order_id, cmd.size, pc.chain_shares, pc.shares
            """
        ).fetchall()
    repairable: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            filled_size = float(row["filled_size"] or 0.0)
            command_size = float(row["command_size"] or 0.0)
            position_shares = float(row["position_shares"] or 0.0)
            fill_notional = float(row["fill_notional"] or 0.0)
        except (TypeError, ValueError):
            continue
        target_size = max(command_size, position_shares)
        if target_size <= 0.0 or filled_size + 1e-9 < target_size or fill_notional <= 0.0:
            continue
        repairable[str(row["position_id"])] = {
            "command_id": row["command_id"],
            "venue_order_id": row["venue_order_id"],
            "filled_size": filled_size,
            "target_size": target_size,
            "avg_fill_price": fill_notional / filled_size if filled_size > 0 else None,
            "trade_states": row["trade_states"],
            "observed_at": row["observed_at"],
        }
    return repairable


def _exit_retry_resumable_by_position() -> dict[str, dict[str, Any]]:
    with _connect_live_ro() as conn:
        if not (
            _table_exists(conn, "main", "venue_commands")
            and _table_exists(conn, "main", "position_current")
        ):
            return {}
        rows = conn.execute(
            """
            WITH latest_exit AS (
                SELECT cmd.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY cmd.position_id
                           ORDER BY datetime(cmd.updated_at) DESC, cmd.command_id DESC
                       ) AS rn
                  FROM venue_commands cmd
                 WHERE cmd.intent_kind = 'EXIT'
            )
            SELECT pc.position_id,
                   pc.exit_retry_count,
                   pc.next_exit_retry_at,
                   latest_exit.command_id,
                   latest_exit.state AS command_state,
                   latest_exit.venue_order_id,
                   latest_exit.updated_at AS command_updated_at
              FROM position_current pc
              JOIN latest_exit
                ON latest_exit.position_id = pc.position_id
               AND latest_exit.rn = 1
             WHERE pc.phase = 'pending_exit'
               AND COALESCE(pc.exit_retry_count, 0) > 0
               AND COALESCE(pc.next_exit_retry_at, '') != ''
            """
        ).fetchall()
    resumable: dict[str, dict[str, Any]] = {}
    terminal_no_resting = {"REJECTED", "EXPIRED", "FAILED", "CANCELED", "CANCELLED", "FILLED"}
    for row in rows:
        state = str(row["command_state"] or "").upper()
        venue_order_id = str(row["venue_order_id"] or "")
        if state not in terminal_no_resting:
            continue
        if state == "FILLED":
            continue
        resumable[str(row["position_id"])] = {
            "command_id": row["command_id"],
            "command_state": row["command_state"],
            "venue_order_id": venue_order_id,
            "exit_retry_count": row["exit_retry_count"],
            "next_exit_retry_at": row["next_exit_retry_at"],
            "command_updated_at": row["command_updated_at"],
        }
    with _connect_live_ro() as conn:
        if not (
            _table_exists(conn, "main", "venue_commands")
            and _table_exists(conn, "main", "position_current")
            and _table_exists(conn, "main", "position_events")
        ):
            return resumable
        rows = conn.execute(
            """
            WITH latest_exit AS (
                SELECT cmd.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY cmd.position_id
                           ORDER BY datetime(cmd.updated_at) DESC, cmd.command_id DESC
                       ) AS rn
                  FROM venue_commands cmd
                 WHERE cmd.intent_kind = 'EXIT'
            ),
            latest_event AS (
                SELECT pe.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY pe.position_id
                           ORDER BY pe.sequence_no DESC, datetime(pe.occurred_at) DESC
                       ) AS rn
                  FROM position_events pe
                 WHERE pe.event_type IN ('EXIT_ORDER_REJECTED', 'EXIT_INTENT', 'EXIT_ORDER_POSTED')
            )
            SELECT pc.position_id,
                   pc.exit_retry_count,
                   pc.next_exit_retry_at,
                   latest_event.event_id,
                   latest_event.event_type,
                   latest_event.venue_status,
                   latest_event.occurred_at
              FROM position_current pc
              LEFT JOIN latest_exit
                ON latest_exit.position_id = pc.position_id
               AND latest_exit.rn = 1
              JOIN latest_event
                ON latest_event.position_id = pc.position_id
               AND latest_event.rn = 1
             WHERE pc.phase = 'pending_exit'
               AND latest_exit.command_id IS NULL
               AND latest_event.event_type = 'EXIT_ORDER_REJECTED'
               AND LOWER(COALESCE(latest_event.venue_status, '')) = 'retry_pending'
               AND COALESCE(pc.exit_retry_count, 0) > 0
               AND COALESCE(pc.next_exit_retry_at, '') != ''
            """
        ).fetchall()
    for row in rows:
        resumable[str(row["position_id"])] = {
            "command_id": None,
            "command_state": "NO_EXIT_COMMAND_RETRY_PENDING",
            "venue_order_id": "",
            "exit_retry_count": row["exit_retry_count"],
            "next_exit_retry_at": row["next_exit_retry_at"],
            "command_updated_at": None,
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "venue_status": row["venue_status"],
            "event_occurred_at": row["occurred_at"],
            "restart_resolution": "exit_lifecycle_pre_submit_retry_resume",
        }
    return resumable


def _single_family_reseed_repair_evidence(item: dict[str, Any]) -> dict[str, Any] | None:
    """Read-only proof that the production single-family reseed lane can repair missing belief.

    This does not enqueue or materialize. It verifies the same materializable-family condition the
    live reseed path will use after restart: a current raw manifest exists for this exact
    (city, target_date, metric), so a missing posterior can be first-materialized automatically.
    """
    try:
        from src.data.replacement_cycle_advance_trigger import (
            family_materializable_cycle,
            freshest_materializable_cycle,
        )
        from src.data.replacement_forecast_production import (
            _replacement_forecast_live_materialization_queue_config,
        )
        from src.data.replacement_forecast_seed_discovery import (
            _latest_manifest,
            _load_manifests,
        )
        from src.data.replacement_forecast_source_run_identity import (
            expected_replacement_dependency_identity_by_role,
        )

        cfg = _replacement_forecast_live_materialization_queue_config()
        raw_manifest_dir = cfg.get("raw_manifest_dir")
        if raw_manifest_dir is None:
            return None
        now = datetime.now(timezone.utc)
        manifests = _load_manifests(Path(str(raw_manifest_dir)), computed_at=now)
        from src.state.db import _connect

        conn = _connect(Path(FORECAST_DB), write_class="live")
        try:
            conn.execute("PRAGMA query_only=ON")
            freshest = freshest_materializable_cycle(conn)
            family_cycle, missing = family_materializable_cycle(
                manifests,
                city=str(item["city"]),
                target_date=str(item["target_date"]),
                metric=str(item["temperature_metric"]),
                expected_identity=expected_replacement_dependency_identity_by_role,
                latest_manifest=_latest_manifest,
            )
        finally:
            conn.close()
        if family_cycle is None:
            return None
        evidence = {
            **item,
            "risk": "missing_live_belief_repairable_by_single_family_reseed",
            "freshest_materializable_cycle": None if freshest is None else freshest.isoformat(),
            "family_materializable_cycle": family_cycle.isoformat(),
            "missing_legs": [list(row) for row in missing],
            "repair_lane": "enqueue_single_family_cycle_advance_reseed",
            "write_performed": False,
        }
        return evidence
    except Exception:
        return None


def _latest_monitor_projection_evidence(
    position_id: str,
    *,
    day0_required: bool,
) -> dict[str, Any] | None:
    """Return fresh monitor belief evidence from the trade event projection.

    Non-Day0 held positions may refresh belief through the canonical replacement
    read-through path before the durable forecast_posteriors row is re-materialized.
    Day0 positions are stricter, but the live monitor has a defined fallback when
    the Day0 observation source is temporarily unavailable: a fresh replacement
    posterior is acceptable only when the same monitor receipt explicitly records
    ``day0_observation_unavailable:replacement_posterior_fresh``. A bare forecast
    posterior does not satisfy a Day0 monitor belief.
    """

    now = datetime.now(timezone.utc)
    try:
        with _connect_live_ro() as conn:
            row = conn.execute(
                """
                SELECT occurred_at, payload_json
                  FROM position_events
                 WHERE position_id = ?
                   AND event_type = 'MONITOR_REFRESHED'
                 ORDER BY datetime(occurred_at) DESC, sequence_no DESC
                 LIMIT 1
                """,
                (position_id,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    occurred_at = str(row["occurred_at"] or "")
    occurred_dt = _parse_dt(occurred_at)
    if occurred_dt is None:
        return None
    age_seconds = (now - occurred_dt).total_seconds()
    if age_seconds < 0.0 or age_seconds > MONITOR_PROJECTION_MAX_AGE_SECONDS:
        return None
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except Exception:
        payload = {}
    validations_raw = payload.get("applied_validations") if isinstance(payload, dict) else None
    validations = [str(item) for item in validations_raw] if isinstance(validations_raw, list) else []
    if day0_required:
        accepted_day0_observation = any(
            item == "day0_observation_remaining_window"
            or item == "day0_absorbing_hard_fact"
            or item.startswith("belief_source=day0_observation_remaining_window")
            or item.startswith("belief_source=day0_absorbing_hard_fact")
            for item in validations
        )
        accepted_replacement_fallback = (
            "day0_observation_unavailable:replacement_posterior_fresh" in validations
            and any(item.startswith("belief_source=forecast_posteriors") for item in validations)
        )
        if not (accepted_day0_observation or accepted_replacement_fallback):
            return None
        source = (
            "day0_monitor_observation_authority"
            if accepted_day0_observation
            else "day0_monitor_replacement_fallback"
        )
    else:
        accepted = any(
            item == "replacement_posterior"
            or item.startswith("belief_source=forecast_posteriors")
            for item in validations
        )
        if not accepted:
            return None
        source = "monitor_replacement_authority"
    return {
        "position_id": position_id,
        "occurred_at": occurred_at,
        "age_seconds": age_seconds,
        "source": source,
        "accepted_validations": [
            item
            for item in validations
            if item == "replacement_posterior"
            or item == "day0_observation_remaining_window"
            or item == "day0_absorbing_hard_fact"
            or item == "day0_observation_unavailable:replacement_posterior_fresh"
            or item.startswith("belief_source=")
        ][:6],
        "max_age_seconds": MONITOR_PROJECTION_MAX_AGE_SECONDS,
    }


def _belief_check(rows: list[sqlite3.Row]) -> CheckResult:
    from src.engine.position_belief import load_replacement_belief, monitor_belief_max_age_hours
    from src.data.replacement_forecast_cycle_policy import replacement_source_cycle_max_age_hours

    risky: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    repairable: list[dict[str, Any]] = []
    settlement_recoverable: list[dict[str, Any]] = []
    max_age = monitor_belief_max_age_hours()
    source_cycle_max_age = replacement_source_cycle_max_age_hours()
    settlement_truth = _verified_settlement_truth_for(rows)
    harvester_enabled, harvester_evidence = _harvester_live_enabled()
    for row in rows:
        if row["phase"] == "pending_exit":
            continue
        item = {
            "position_id": row["position_id"],
            "phase": row["phase"],
            "city": row["city"],
            "target_date": row["target_date"],
            "temperature_metric": row["temperature_metric"],
            "bin_label": row["bin_label"],
            "direction": row["direction"],
        }
        settlement = settlement_truth.get(
            (
                str(row["city"] or ""),
                str(row["target_date"] or ""),
                str(row["temperature_metric"] or "high").lower(),
            )
        )
        if settlement is not None:
            evidence = {
                **item,
                "risk": "verified_settlement_pending_harvester_recovery",
                "settlement": settlement,
                "harvester_live_enabled": harvester_enabled,
            }
            if harvester_enabled:
                settlement_recoverable.append(evidence)
                continue
            risky.append({**evidence, "risk": "settled_position_harvester_disabled"})
            continue

        if str(row["phase"] or "") == "day0_window":
            monitor_evidence = _latest_monitor_projection_evidence(
                str(row["position_id"] or ""),
                day0_required=True,
            )
            if monitor_evidence is not None:
                covered.append(
                    {
                        **item,
                        "fresh": True,
                        "freshness_basis": "day0_monitor_projection",
                        "monitor_projection": monitor_evidence,
                    }
                )
                continue
            canonical_evidence = _day0_canonical_observation_evidence(row, now=datetime.now(timezone.utc))
            if canonical_evidence is not None:
                covered.append(
                    {
                        **item,
                        "fresh": True,
                        "freshness_basis": "day0_canonical_observation_boot_redecision",
                        "restart_resolution": "boot_monitor_refresh_from_canonical_day0_observation",
                        "canonical_observation": canonical_evidence,
                    }
                )
                continue
            risky.append({**item, "risk": "day0_monitor_observation_belief_missing_or_stale"})
            continue

        active_day0_monitor_evidence = _latest_monitor_projection_evidence(
            str(row["position_id"] or ""),
            day0_required=True,
        )
        if active_day0_monitor_evidence is not None:
            covered.append(
                {
                    **item,
                    "fresh": True,
                    "freshness_basis": "active_day0_monitor_projection",
                    "monitor_projection": active_day0_monitor_evidence,
                }
            )
            continue

        belief = load_replacement_belief(
            city=str(row["city"] or ""),
            target_date=str(row["target_date"] or ""),
            temperature_metric=str(row["temperature_metric"] or "high"),
            bin_label=str(row["bin_label"] or ""),
            direction=str(row["direction"] or ""),
            max_age_hours=max_age,
            db_path=str(FORECAST_DB),
        )
        if belief is None:
            repair = _single_family_reseed_repair_evidence(item)
            if repair is not None:
                repairable.append(repair)
                risky.append({**item, "risk": "missing_live_belief_repairable_only_not_materialized"})
                continue
            risky.append({**item, "risk": "missing_live_belief"})
            continue
        evidence = {
            **item,
            "posterior_id": belief.posterior_id,
            "computed_at": belief.computed_at,
            "age_hours": belief.age_hours,
            "source_cycle_age_hours": belief.source_cycle_age_hours,
            "fresh": belief.fresh,
            "held_side_prob": belief.held_side_prob,
            "q_yes_bin": belief.q_yes_bin,
            "freshness_basis": belief.freshness_basis,
        }
        covered.append(evidence)
        if not belief.fresh:
            repair = _single_family_reseed_repair_evidence({**item, **evidence})
            if repair is not None:
                repairable.append(
                    {
                        **repair,
                        "risk": "stale_live_belief_repairable_by_single_family_reseed",
                        "posterior_id": belief.posterior_id,
                        "computed_at": belief.computed_at,
                        "age_hours": belief.age_hours,
                        "source_cycle_age_hours": belief.source_cycle_age_hours,
                        "freshness_basis": belief.freshness_basis,
                    }
                )
                risky.append({**evidence, "risk": "stale_live_belief_repairable_only_not_materialized"})
                continue
            risky.append({**evidence, "risk": "stale_live_belief"})
    return CheckResult(
        "held_position_belief_coverage",
        not risky,
        "all active held positions have fresh live belief, verified settlement recovery, or repairable reseed"
        if not risky
        else "active held positions have stale/missing live belief or blocked settlement recovery",
        {
            "risky": risky,
            "covered": covered,
            "repairable": repairable,
            "settlement_recoverable": settlement_recoverable,
            "computed_at_fallback_max_age_hours": max_age,
            "source_cycle_max_age_hours": source_cycle_max_age,
            "freshness_contract": (
                "source_cycle_time uses replacement source-cycle staleness; "
                "computed_at max age is only the fallback for rows without source_cycle_time"
            ),
            "harvester_live_enabled": harvester_enabled,
            "harvester_evidence": harvester_evidence,
        },
    )


def evaluate() -> dict[str, Any]:
    cfg = _settings()
    real_submit = bool((cfg.get("edli") or {}).get("real_order_submit_enabled", False))
    rows = _open_positions()
    projection_rows = _open_positions(positive_chain_only=False)
    quote_rows = _open_positions_requiring_executable_quote(rows)
    edli_cfg = cfg.get("edli") or {}
    reactor_mode = str(edli_cfg.get("reactor_mode") or "disabled")
    live_execution_mode = str(edli_cfg.get("live_execution_mode") or "missing")
    armed_live = live_execution_mode == "edli_live"
    known_execution_mode = live_execution_mode in {"edli_live", "maker", "disabled"}
    real_submit_effective = real_submit and reactor_mode == "live"
    submit_ok = known_execution_mode and ((not armed_live) or real_submit_effective)
    checks = [
        _live_trading_launchagent_installed_check(),
        CheckResult(
            "live_trading_process_absent",
            not _live_main_processes(),
            "src.main is not running" if not _live_main_processes() else "src.main is already running",
            {"processes": _live_main_processes()},
        ),
        CheckResult(
            "submit_authority_config",
            submit_ok,
            "real order submit is enabled for armed live restart"
            if submit_ok
            else (
                "live_execution_mode must be explicit (edli_live/maker/disabled), and "
                "armed live restart requires real_order_submit_enabled with reactor_mode=live"
            ),
            {
                "edli.real_order_submit_enabled": real_submit,
                "edli.reactor_mode": reactor_mode,
                "edli.live_execution_mode": live_execution_mode,
                "known_execution_mode": known_execution_mode,
                "armed_live": armed_live,
                "real_submit_effective": real_submit_effective,
            },
        ),
        _clob_signature_type_config_check(required=real_submit_effective),
        _qkernel_spine_cutover_check(cfg),
        _src_main_boot_guard_check(),
        _family_portfolio_single_leg_check(),
        _qlcb_reliability_artifact_check(),
        _forecast_sidecar_health(),
        _posterior_summary(),
        *_sidecar_heartbeat_checks(),
        _collateral_snapshot_freshness_check(),
        _edli_live_order_presubmit_shape_check(),
        _live_actionable_certificate_semantics_check(),
        _live_money_certificate_parent_mode_check(),
        _venue_point_order_truth_alignment_check(),
        _position_current_projection_integrity_check(projection_rows),
        _resting_venue_command_lifecycle_alignment_check(),
        _full_family_executable_substrate_redecision_check(quote_rows),
        _execution_feasibility_evidence_check(quote_rows),
        _pending_exit_check(rows),
        _belief_check(rows),
    ]
    blockers = [asdict(check) for check in checks if not check.ok]
    return {
        "ok": not blockers,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(),
        "trade_db": str(TRADE_DB),
        "forecast_db": str(FORECAST_DB),
        "open_position_count": len(rows),
        "runtime_open_projection_count": len(projection_rows),
        "open_positions_requiring_executable_quote_count": len(quote_rows),
        "real_order_submit_enabled": real_submit,
        "checks": [asdict(check) for check in checks],
        "blockers": blockers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)
    result = evaluate()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"live restart preflight: {'PASS' if result['ok'] else 'FAIL'}")
        print(f"generated_at={result['generated_at']} git_head={result['git_head']}")
        for check in result["checks"]:
            status = "PASS" if check["ok"] else "FAIL"
            print(f"{status} {check['name']}: {check['detail']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
