#!/usr/bin/env python3
# Lifecycle: created=2026-06-18; last_reviewed=2026-06-19; last_reused=2026-06-19
# Purpose: Read-only preflight before restarting the live trading daemon.
# Reuse: Run immediately before loading com.zeus.live-trading or python -m src.main.
# Created: 2026-06-18
# Last reused or audited: 2026-06-19
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
from datetime import datetime, timezone
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
SIDECAR_HEARTBEATS = (
    ("substrate_observer_daemon", "daemon-heartbeat-substrate-observer.json"),
    ("price_channel_daemon", "daemon-heartbeat-price-channel-ingest.json"),
    ("post_trade_capital_daemon", "daemon-heartbeat-post-trade-capital.json"),
)
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


def _settings() -> dict[str, Any]:
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except Exception:
        return {}


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
            exposures=[_open_exposure_identity(row) for row in rows],
            now=now,
        )
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


def _executable_substrate_freshness_check(rows: list[sqlite3.Row]) -> CheckResult:
    now = datetime.now(timezone.utc)
    evidence: dict[str, Any] = {
        "table": "executable_market_snapshots",
        "max_age_seconds": EXECUTABLE_SUBSTRATE_MAX_AGE_SECONDS,
    }
    if not rows:
        evidence["scoped_exposure_count"] = 0
        evidence["row_count"] = "not_scanned_no_open_exposures"
        return CheckResult(
            "executable_substrate_freshness",
            True,
            "no open exposures require executable market substrate",
            evidence,
        )
    with _connect_live_ro() as conn:
        if not _table_exists(conn, "main", "executable_market_snapshots"):
            return CheckResult(
                "executable_substrate_freshness",
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
    ok = (age_ok or deadline_ok) and not exposure_results["risky"]
    return CheckResult(
        "executable_substrate_freshness",
        ok,
        (
            "executable market substrate is fresh for open exposures"
            if ok
            else "executable market substrate is stale/missing for open exposures"
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

    The preflight usually runs from an operator shell, not inside launchd, so the
    shell environment alone is not enough evidence. Prefer the current process
    env when present, then inspect the launchd plist that owns src.main.
    """
    env_value = os.environ.get("ZEUS_HARVESTER_LIVE_ENABLED")
    evidence: dict[str, Any] = {
        "env_value": env_value,
        "plist_path": str(LIVE_TRADING_PLIST_PATH),
        "plist_value": None,
        "source": "env" if env_value is not None else "plist",
    }
    if env_value is not None:
        return env_value == "1", evidence

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
        item = {
            "status": status,
            "last_run_at": entry.get("last_run_at"),
            "last_started_at": entry.get("last_started_at"),
            "last_success_at": entry.get("last_success_at"),
            "last_failure_at": entry.get("last_failure_at"),
            "last_failure_reason": entry.get("last_failure_reason"),
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
    age_hours = None
    if latest_dt is not None:
        age_hours = (now - latest_dt).total_seconds() / 3600.0
    non_live = sum(int(row["rows"]) for row in runtime_rows if row["runtime_layer"] != "live")
    ok = non_live == 0 and age_hours is not None and 0.0 <= age_hours <= 3.0
    return CheckResult(
        "live_posterior_freshness",
        ok,
        "latest live posterior is fresh" if ok else "latest live posterior is stale/missing or non-live rows exist",
        {
            "runtime_layers": runtime_rows,
            "latest_live_computed_at": latest["computed_at"] if latest else None,
            "latest_live_age_hours": age_hours,
            "non_live_rows": non_live,
            "fresh_age_limit_hours": 3.0,
        },
    )


def _open_positions() -> list[Any]:
    with _connect_live_ro() as conn:
        columns = _table_columns(conn, "main", "position_current")
        optional_selects = []
        for column in ("condition_id", "token_id", "no_token_id"):
            optional_selects.append(column if column in columns else f"NULL AS {column}")
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
                   AND COALESCE(chain_shares, shares, 0) > 0
                 ORDER BY CASE phase WHEN 'pending_exit' THEN 0 ELSE 1 END,
                          city, target_date, bin_label
                """
            )
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


def _belief_check(rows: list[sqlite3.Row]) -> CheckResult:
    from src.engine.position_belief import load_replacement_belief, monitor_belief_max_age_hours

    risky: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    repairable: list[dict[str, Any]] = []
    settlement_recoverable: list[dict[str, Any]] = []
    max_age = monitor_belief_max_age_hours()
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
            "max_age_hours": max_age,
            "harvester_live_enabled": harvester_enabled,
            "harvester_evidence": harvester_evidence,
        },
    )


def evaluate() -> dict[str, Any]:
    cfg = _settings()
    real_submit = bool((cfg.get("edli") or {}).get("real_order_submit_enabled", False))
    rows = _open_positions()
    quote_rows = _open_positions_requiring_executable_quote(rows)
    checks = [
        CheckResult(
            "live_trading_process_absent",
            not _live_main_processes(),
            "src.main is not running" if not _live_main_processes() else "src.main is already running",
            {"processes": _live_main_processes()},
        ),
        CheckResult(
            "submit_authority_config",
            True,
            "real order submit config read",
            {"edli.real_order_submit_enabled": real_submit},
        ),
        _qkernel_spine_cutover_check(cfg),
        _family_portfolio_single_leg_check(),
        _qlcb_reliability_artifact_check(),
        _forecast_sidecar_health(),
        _posterior_summary(),
        *_sidecar_heartbeat_checks(),
        _collateral_snapshot_freshness_check(),
        _executable_substrate_freshness_check(quote_rows),
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
