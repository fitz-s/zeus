# Created: 2026-05-19
# Last reused or audited: 2026-06-19
# Authority basis: codereview-may19-2.md relationship F
#                  + docs/operations/task_2026-05-21_live_side_effect_risk_boundaries/task.md P1-1
#
# Composite live-health: heartbeat OK AND latest run_mode OK AND
# status_summary fresh AND no surface is silently stale.
#
# Closes Relationship Finding F: scheduler can appear alive (heartbeat OK,
# process running) while run_mode has failed. This module surfaces a
# composite signal so operators/healthcheck see the real picture.

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATUS_FRESH_BUDGET_SECONDS = 300  # 5 minutes — consistent with heartbeat budget
FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS = STATUS_FRESH_BUDGET_SECONDS
DAY0_DECISION_TRACE_LOOKBACK_SECONDS = 3600
DAY0_DECISION_TRACE_SAMPLE_LIMIT = 50
FORECAST_PIPELINE_HEALTH_JOBS = (
    "bayes_precision_fusion_capture",
    "replacement_forecast_download",
    "replacement_forecast_live_materialize",
)
LEGACY_CRON_RUN_MODE_PREFIX = "run_mode:"
ENTRY_Q_VERSION_LOOKBACK_SECONDS = 2 * 3600
ENTRY_Q_VERSION_SAMPLE_LIMIT = 20
PENDING_EXIT_RELEASE_LOOP_LOOKBACK_SECONDS = 30 * 60
PENDING_EXIT_CHURN_LOOKBACK_SECONDS = 24 * 3600
PENDING_EXIT_RELEASE_LOOP_SAMPLE_LIMIT = 10
PENDING_EXIT_REASSERT_LOOP_MIN_INTENTS = 3
PENDING_EXIT_CHURN_MIN_INTENTS = 10
PENDING_EXIT_CHURN_MIN_REJECTIONS_OR_RELEASES = 3
MONITOR_PROBABILITY_STALE_LOOKBACK_SECONDS = 10 * 60
MONITOR_PROBABILITY_STALE_SAMPLE_LIMIT = 10
PROCESS_CODE_STALE_TOLERANCE_SECONDS = 2
LIVE_DAEMON_PROCESS_CODE_PATHS = (
    "src/main.py",
    "src/control/cutover_guard.py",
    "src/control/heartbeat_supervisor.py",
    "src/control/live_health.py",
    "src/control/runtime_code_plane.py",
    "src/control/ws_gap_guard.py",
    "src/contracts/executable_market_snapshot.py",
    "src/contracts/execution_intent.py",
    "src/contracts/no_trade_reason.py",
    "src/data/market_scanner.py",
    "src/data/polymarket_client.py",
    "src/engine/cycle_runner.py",
    "src/engine/cycle_runtime.py",
    "src/engine/evaluator.py",
    "src/engine/event_reactor_adapter.py",
    "src/engine/monitor_refresh.py",
    "src/engine/position_belief.py",
    "src/events/event_store.py",
    "src/events/reactor.py",
    "src/execution/command_recovery.py",
    "src/execution/exchange_reconcile.py",
    "src/execution/executor.py",
    "src/execution/exit_lifecycle.py",
    "src/execution/exit_safety.py",
    "src/execution/harvester_pnl_resolver.py",
    "src/execution/staleness_cancel.py",
    "src/observability/status_summary.py",
    "src/state/chain_mirror_reconciler.py",
    "src/state/db.py",
    "src/strategy/family_exclusive_dedup.py",
    "src/strategy/selection_family.py",
)


def _state_dir(override: Optional[Path]) -> Path:
    if override is not None:
        return override
    from src.config import state_path
    return state_path("dummy").parent


def _read_json(path: Path) -> Optional[dict]:
    """Return parsed dict or None on any read/parse failure."""
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _current_git_head() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_repo_root()),
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    head = proc.stdout.strip()
    return head or None


def _dirty_runtime_worktree_paths(*, timeout: float = 2.0) -> tuple[str, ...]:
    """Return dirty runtime-plane paths so SHA equality is not overclaimed."""

    from src.control.runtime_code_plane import dirty_runtime_worktree_paths

    return dirty_runtime_worktree_paths(_repo_root(), timeout=timeout)


def _runtime_code_surface(state_dir: Path) -> dict:
    payload = _read_json(state_dir / "loaded_sha.json")
    if payload is None:
        return {"ok": False, "issue": "LOADED_SHA_MISSING"}
    loaded_sha = str(
        payload.get("loaded_sha")
        or payload.get("boot_sha")
        or payload.get("current_sha")
        or ""
    ).strip()
    if not loaded_sha:
        return {"ok": False, "issue": "LOADED_SHA_EMPTY", "loaded_sha": loaded_sha}
    if not _is_full_git_sha(loaded_sha):
        return {
            "ok": False,
            "issue": f"LOADED_SHA_INVALID:loaded={loaded_sha}",
            "loaded_sha": loaded_sha,
        }
    from src.control.runtime_code_plane import runtime_code_plane_diff

    code_plane = runtime_code_plane_diff(
        _repo_root(),
        boot_sha=loaded_sha,
        timeout=2.0,
    )
    dirty_paths = _dirty_runtime_worktree_paths(timeout=2.0)
    dirty_detail = {
        "worktree_runtime_dirty": bool(dirty_paths),
        "dirty_runtime_paths_sample": list(dirty_paths[:20]),
    }
    current_sha = code_plane.current_sha
    if not current_sha:
        return {
            "ok": False,
            "issue": "CURRENT_GIT_HEAD_UNAVAILABLE",
            "loaded_sha": loaded_sha,
            **dirty_detail,
        }
    if code_plane.error:
        return {
            "ok": False,
            "issue": (
                f"LOADED_SHA_MISMATCH:loaded={loaded_sha}:current={current_sha}:"
                f"code_plane={code_plane.status}:{code_plane.error}"
            ),
            "loaded_sha": loaded_sha,
            "current_sha": current_sha,
            "code_plane_status": code_plane.status,
            **dirty_detail,
        }
    if loaded_sha != current_sha and code_plane.runtime_code_changed:
        return {
            "ok": False,
            "issue": (
                f"LOADED_SHA_MISMATCH:loaded={loaded_sha}:current={current_sha}:"
                f"code_plane={code_plane.status}"
            ),
            "loaded_sha": loaded_sha,
            "current_sha": current_sha,
            "code_plane_status": code_plane.status,
            "changed_paths_sample": list(code_plane.changed_paths[:20]),
            **dirty_detail,
        }
    if dirty_paths:
        return {
            "ok": False,
            "issue": "RUNTIME_WORKTREE_DIRTY",
            "loaded_sha": loaded_sha,
            "current_sha": current_sha,
            "code_plane_status": code_plane.status,
            "changed_paths_sample": list(code_plane.changed_paths[:20]),
            **dirty_detail,
        }
    return {
        "ok": True,
        "issue": None,
        "loaded_sha": loaded_sha,
        "current_sha": current_sha,
        "code_plane_status": code_plane.status,
        "changed_paths_sample": list(code_plane.changed_paths[:20]),
        **dirty_detail,
    }


def _is_full_git_sha(value: object) -> bool:
    text = str(value or "").strip()
    return len(text) == 40 and all(ch in "0123456789abcdefABCDEF" for ch in text)


def _int_or_none(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _process_command_line(pid: int) -> str | None:
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    command = proc.stdout.strip()
    return command or None


def _looks_like_live_main_command(command: str) -> bool:
    normalized = command.replace("\\", "/")
    return " -m src.main" in normalized or normalized.endswith(" -m src.main") or "src/main.py" in normalized


def _main_daemon_surface(status_summary: Optional[dict], heartbeat: Optional[dict]) -> dict:
    """Attest the main live daemon process when current surfaces cite a PID."""

    status_process = status_summary.get("process") if isinstance(status_summary, dict) else None
    status_pid = (
        _int_or_none(status_process.get("pid"))
        if isinstance(status_process, dict)
        else None
    )
    heartbeat_pid = _int_or_none(heartbeat.get("pid")) if isinstance(heartbeat, dict) else None
    if status_pid is None and heartbeat_pid is None:
        return {"ok": True, "issue": None, "attested": False, "pid": None}
    if status_pid is not None and heartbeat_pid is not None and status_pid != heartbeat_pid:
        return {
            "ok": False,
            "issue": "MAIN_DAEMON_PID_MISMATCH",
            "status_pid": status_pid,
            "heartbeat_pid": heartbeat_pid,
        }
    pid = status_pid or heartbeat_pid
    assert pid is not None
    command = _process_command_line(pid)
    if command is None:
        return {
            "ok": False,
            "issue": "MAIN_DAEMON_PROCESS_NOT_FOUND",
            "pid": pid,
        }
    if not _looks_like_live_main_command(command):
        return {
            "ok": False,
            "issue": "MAIN_DAEMON_COMMAND_MISMATCH",
            "pid": pid,
            "command": command,
        }
    return {
        "ok": True,
        "issue": None,
        "attested": True,
        "pid": pid,
        "command": command,
    }


def _process_start_epoch(pid: int) -> float | None:
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    if not text:
        return None
    try:
        return time.mktime(time.strptime(text, "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def _latest_source_mtime(
    repo_root: Path,
    rel_paths: tuple[str, ...] = LIVE_DAEMON_PROCESS_CODE_PATHS,
) -> tuple[float | None, str | None]:
    latest_mtime: float | None = None
    latest_path: str | None = None
    for rel_path in rel_paths:
        try:
            mtime = (repo_root / rel_path).stat().st_mtime
        except OSError:
            continue
        if latest_mtime is None or mtime > latest_mtime:
            latest_mtime = mtime
            latest_path = rel_path
    return latest_mtime, latest_path


def _process_code_surface(main_daemon_surface: dict) -> dict:
    """Attest that the running daemon is newer than source it is expected to load."""

    if not bool(main_daemon_surface.get("attested")):
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_MAIN_DAEMON_NOT_ATTESTED",
            "evaluated": False,
        }
    pid = _int_or_none(main_daemon_surface.get("pid"))
    started_at = _process_start_epoch(pid) if pid is not None else None
    source_mtime, source_path = _latest_source_mtime(_repo_root())
    detail = {
        "evaluated": True,
        "pid": pid,
        "started_at": started_at,
        "source_mtime": source_mtime,
        "source_path": source_path,
        "stale_tolerance_seconds": PROCESS_CODE_STALE_TOLERANCE_SECONDS,
        "paths_sample": list(LIVE_DAEMON_PROCESS_CODE_PATHS[:20]),
    }
    if pid is None or started_at is None or source_mtime is None:
        return {
            "ok": False,
            "issue": "PROCESS_LOADED_CODE_UNATTESTED",
            **detail,
        }
    if started_at + PROCESS_CODE_STALE_TOLERANCE_SECONDS < source_mtime:
        return {
            "ok": False,
            "issue": "PROCESS_LOADED_CODE_STALE",
            **detail,
        }
    return {"ok": True, "issue": None, **detail}


def _age_seconds(ts_str: str, now: datetime) -> Optional[float]:
    """Return age in seconds for an ISO timestamp string, or None if unparseable."""
    try:
        # Handle both offset-aware and naive ISO strings
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = now - ts
        return delta.total_seconds()
    except (ValueError, TypeError):
        return None


def _parse_iso_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _int_value(payload: dict, *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _mapping_has_positive_counter(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    for value in payload.values():
        if isinstance(value, dict):
            if _mapping_has_positive_counter(value):
                return True
            continue
        try:
            if int(value) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _has_text_value(payload: dict, *keys: str) -> bool:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _forecast_pipeline_surface(scheduler_health: Optional[dict]) -> dict:
    """Replacement/BPF forecast production status from scheduler health.

    The live daemon can be healthy while the forecast sidecar is alive but not
    producing usable BPF inputs. Only current replacement/BPF production jobs are
    considered here; older disabled OpenData job remnants remain outside this
    surface to avoid turning history into a live blocker.
    """

    if scheduler_health is None:
        return {"ok": True, "issue": None, "checked_jobs": []}
    checked: list[str] = []
    failed: list[tuple[str, dict]] = []
    for job in FORECAST_PIPELINE_HEALTH_JOBS:
        entry = scheduler_health.get(job)
        if not isinstance(entry, dict):
            continue
        checked.append(job)
        if str(entry.get("status", "")).upper() == "FAILED":
            failed.append((job, entry))
    if failed:
        job, entry = sorted(failed)[0]
        reason = entry.get("last_failure_reason") or "unknown"
        return {
            "ok": False,
            "issue": f"FORECAST_PIPELINE_FAILED[{job}]: {reason}",
            "checked_jobs": checked,
        }
    return {"ok": True, "issue": None, "checked_jobs": checked}


def _live_execution_mode() -> str:
    try:
        from src.config import settings

        edli = settings.get("edli", {}) if hasattr(settings, "get") else {}
        if isinstance(edli, dict):
            return str(edli.get("live_execution_mode") or "")
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _run_mode_health_entries(scheduler_health: dict, *, live_execution_mode: str) -> list[tuple[str, dict]]:
    """Return run-mode health rows relevant to the active scheduler topology.

    In ``edli_live`` the legacy cron modes are not registered by ``src.main``. Old durable
    ``run_mode:*`` rows may remain in scheduler_jobs_health.json, but consuming them as current
    liveness turns history into a live signal. In ``legacy_cron`` those rows are active and remain
    part of the surface.
    """

    entries: list[tuple[str, dict]] = []
    include_legacy_mode_rows = live_execution_mode == "legacy_cron"
    for key, value in scheduler_health.items():
        key_str = str(key)
        if not isinstance(value, dict):
            continue
        if key_str in {"_run_mode", "run_mode"}:
            entries.append((key_str, value))
            continue
        if include_legacy_mode_rows and key_str.startswith(LEGACY_CRON_RUN_MODE_PREFIX):
            entries.append((key_str, value))
    return entries


def _business_plane_surface(status_summary: Optional[dict]) -> dict:
    """Derived business-plane progress proof from the latest cycle summary.

    This intentionally does not authorize trading. It prevents health reports
    from collapsing daemon/process liveness into business progress by requiring
    the cycle read-model to expose core counters and by publishing the money-path
    progress flags operators need to distinguish math no-trades from structural
    stalls.
    """

    if status_summary is None:
        return {"ok": False, "issue": "STATUS_SUMMARY_MISSING", "progress": {}}
    cycle = status_summary.get("cycle")
    if not isinstance(cycle, dict):
        return {"ok": False, "issue": "CYCLE_SUMMARY_MISSING", "progress": {}}
    if bool(cycle.get("failed", False)):
        return {
            "ok": False,
            "issue": f"CYCLE_FAILED: {cycle.get('failure_reason') or 'unknown'}",
            "progress": {},
        }
    if bool(cycle.get("skipped", False)):
        return {
            "ok": False,
            "issue": f"CYCLE_SKIPPED: {cycle.get('skip_reason') or 'unknown'}",
            "progress": {},
        }
    if "candidates" not in cycle:
        return {"ok": False, "issue": "CANDIDATE_COUNTER_MISSING", "progress": {}}

    candidates = _int_value(cycle, "candidates", "candidates_evaluated")
    final_intents = _int_value(cycle, "final_intents_built", "final_execution_intents_built")
    submit_attempts = _int_value(
        cycle,
        "submit_attempts",
        "entry_submit_attempts",
        "entry_orders_submitted",
    )
    venue_acks = _int_value(cycle, "venue_acks", "venue_ack_count")
    no_trades = _int_value(cycle, "no_trades", "no_trade_count")
    no_trade_reasons = (
        cycle.get("top_no_trade_reasons")
        or cycle.get("no_trade_reasons")
        or cycle.get("rejection_reason_counts")
    )
    deterministic_rejections = (
        cycle.get("deterministic_rejections")
        or cycle.get("submit_rejections")
        or cycle.get("submit_rejection_reasons")
    )
    zero_candidate_has_proof = (
        _has_text_value(
            cycle,
            "no_market_reason",
            "scanner_no_market_reason",
            "market_discovery_no_market_reason",
            "source_freshness_proof",
        )
        or bool(cycle.get("source_freshness_ok") is True)
        or bool(cycle.get("scanner_attempted") is True)
        or bool(cycle.get("market_scanner_attempted") is True)
    )
    no_trade_reason_proof = (
        _mapping_has_positive_counter(no_trade_reasons)
        or _has_text_value(cycle, "top_no_trade_reason", "dominant_no_trade_reason")
    )
    entry_unavailable_reason = _entry_unavailable_reason(status_summary, cycle)
    entry_unavailable_proof = bool(entry_unavailable_reason)
    deterministic_rejection_observed = _mapping_has_positive_counter(deterministic_rejections)
    command_recovery = cycle.get("command_recovery")
    chain_sync = cycle.get("chain_sync")
    progress = {
        "mode": cycle.get("mode"),
        "last_successful_cycle_at": cycle.get("completed_at") or cycle.get("started_at"),
        "candidates": candidates,
        "candidate_evaluated": candidates > 0,
        "final_intents_built": final_intents,
        "final_intent_built": final_intents > 0,
        "submit_attempts": submit_attempts,
        "submit_attempted": submit_attempts > 0,
        "venue_acks": venue_acks,
        "venue_ack_observed": venue_acks > 0,
        "no_trades": no_trades,
        "no_trade_reason_proof": no_trade_reason_proof,
        "entry_unavailable_proof": entry_unavailable_proof,
        "entry_unavailable_reason": entry_unavailable_reason,
        "zero_candidate_has_proof": zero_candidate_has_proof,
        "deterministic_rejection_observed": deterministic_rejection_observed,
        "reconcile_progress_observed": (
            _mapping_has_positive_counter(command_recovery)
            or _mapping_has_positive_counter(chain_sync)
        ),
    }
    if candidates <= 0 and not zero_candidate_has_proof:
        return {
            "ok": False,
            "issue": "ZERO_CANDIDATES_WITHOUT_SOURCE_OR_NO_MARKET_PROOF",
            "progress": progress,
        }
    if candidates > 0 and final_intents <= 0 and not no_trade_reason_proof and not entry_unavailable_proof:
        return {
            "ok": False,
            "issue": "CANDIDATES_WITHOUT_FINAL_INTENTS_OR_NO_TRADE_REASONS",
            "progress": progress,
        }
    if (
        candidates > 0
        and final_intents <= 0
        and submit_attempts <= 0
        and no_trades > 0
        and no_trade_reason_proof
        and not entry_unavailable_proof
    ):
        return {
            "ok": False,
            "issue": "CANDIDATES_ONLY_NO_TRADE_NO_CAPITAL_FLOW",
            "progress": progress,
        }
    if final_intents > 0 and submit_attempts <= 0:
        return {
            "ok": False,
            "issue": "FINAL_INTENTS_WITHOUT_SUBMIT_ATTEMPTS",
            "progress": progress,
        }
    if submit_attempts > 0 and venue_acks <= 0 and not deterministic_rejection_observed:
        return {
            "ok": False,
            "issue": "SUBMIT_ATTEMPTS_WITHOUT_ACK_OR_DETERMINISTIC_REJECTION",
            "progress": progress,
        }
    return {"ok": True, "issue": None, "progress": progress}


def _entry_unavailable_reason(status_summary: dict, cycle: dict) -> str | None:
    """Return explicit entry-unavailable proof from status/control/cycle read models."""

    control = status_summary.get("control")
    if isinstance(control, dict) and control.get("entries_paused") is True:
        return str(control.get("entries_pause_reason") or "entries_paused")

    execution_capability = status_summary.get("execution_capability")
    if isinstance(execution_capability, dict):
        entry = execution_capability.get("entry")
        if isinstance(entry, dict):
            if entry.get("global_allow_submit") is False:
                reasons: list[str] = []
                for component in entry.get("components") or []:
                    if not isinstance(component, dict) or component.get("allowed") is not False:
                        continue
                    component_name = str(component.get("component") or "entry_component")
                    reason = str(component.get("reason") or "unavailable")
                    reasons.append(f"{component_name}:{reason}")
                if reasons:
                    return ";".join(reasons)
                unavailable_components = entry.get("unavailable_components")
                if isinstance(unavailable_components, list) and unavailable_components:
                    return "entry_unavailable:" + ",".join(str(c) for c in unavailable_components)
                return "entry_global_allow_submit_false"

    allocator = cycle.get("held_monitor_allocator_refresh")
    if isinstance(allocator, dict):
        entry = allocator.get("entry")
        if isinstance(entry, dict) and entry.get("allow_submit") is False:
            return str(entry.get("reason") or "held_monitor_allocator_entry_unavailable")
    blocked_reason = cycle.get("entries_blocked_reason")
    if isinstance(blocked_reason, str) and blocked_reason.strip():
        return blocked_reason.strip()
    return None


def _execution_capability_surface(status_summary: Optional[dict]) -> dict:
    """Surface whether live entry/exit side effects are currently allowed.

    The daemon heartbeat only proves that a process is alive. Live-health must
    also reflect the same execution-capability gate used by order submission;
    otherwise a LOST venue heartbeat can be hidden behind fresh cycle pulses.
    """

    if status_summary is None:
        return {"ok": False, "issue": "STATUS_SUMMARY_MISSING", "actions": {}}
    execution_capability = status_summary.get("execution_capability")
    if not isinstance(execution_capability, dict):
        return {"ok": False, "issue": "EXECUTION_CAPABILITY_MISSING", "actions": {}}

    actions: dict[str, dict] = {}
    blocked: list[str] = []
    for action_name in ("entry", "exit"):
        action = execution_capability.get(action_name)
        if not isinstance(action, dict):
            actions[action_name] = {
                "status": "missing",
                "global_allow_submit": None,
                "unavailable_components": [f"{action_name}_capability_missing"],
                "unavailable_reasons": [],
            }
            blocked.append(f"{action_name}:missing")
            continue

        status = str(action.get("status") or "unknown")
        allow_submit = action.get("global_allow_submit")
        unavailable_components = action.get("unavailable_components")
        if not isinstance(unavailable_components, list):
            unavailable_components = []
        unavailable_reasons = []
        for component in action.get("components") or []:
            if not isinstance(component, dict):
                continue
            if component.get("allowed") is False:
                unavailable_reasons.append(
                    {
                        "component": component.get("component"),
                        "reason": component.get("reason"),
                    }
                )

        action_blocked = (
            status.lower() in {"unavailable", "failed", "error"}
            or allow_submit is False
            or bool(unavailable_components)
            or bool(unavailable_reasons)
        )
        actions[action_name] = {
            "status": status,
            "global_allow_submit": allow_submit,
            "unavailable_components": unavailable_components,
            "unavailable_reasons": unavailable_reasons,
        }
        if action_blocked:
            component_label = ",".join(str(c) for c in unavailable_components) or status
            blocked.append(f"{action_name}:{component_label}")

    if blocked:
        return {
            "ok": False,
            "issue": "LIVE_EXECUTION_CAPABILITY_UNAVAILABLE: " + "; ".join(blocked),
            "actions": actions,
        }
    return {"ok": True, "issue": None, "actions": actions}


def _venue_heartbeat_surface(state_dir: Path, now: datetime) -> dict:
    """Surface external CLOB heartbeat state used by resting-order gates."""

    path = state_dir / "venue-heartbeat-keeper.json"
    payload = _read_json(path)
    if payload is None:
        return {"ok": False, "issue": "VENUE_HEARTBEAT_MISSING"}

    ts_str = payload.get("written_at") or payload.get("last_success_at")
    age = _age_seconds(ts_str, now) if isinstance(ts_str, str) else None
    cadence = payload.get("cadence_seconds")
    try:
        cadence_seconds = float(cadence)
    except (TypeError, ValueError):
        cadence_seconds = 5.0
    max_age = max(8.0, cadence_seconds * 2.0 + 3.0)

    health = str(payload.get("health") or "UNKNOWN").upper()
    resting_order_safe = payload.get("resting_order_safe")
    if age is None:
        return {
            "ok": False,
            "issue": "VENUE_HEARTBEAT_UNPARSEABLE_TIMESTAMP",
            "health": health,
            "resting_order_safe": resting_order_safe,
        }
    if age > max_age:
        return {
            "ok": False,
            "issue": f"VENUE_HEARTBEAT_STALE({age:.0f}s)",
            "health": health,
            "resting_order_safe": resting_order_safe,
            "age_seconds": age,
            "max_age_seconds": max_age,
        }
    if health != "HEALTHY":
        return {
            "ok": False,
            "issue": f"VENUE_HEARTBEAT_{health}",
            "health": health,
            "resting_order_safe": resting_order_safe,
            "last_error": payload.get("last_error"),
            "age_seconds": age,
            "max_age_seconds": max_age,
        }
    if resting_order_safe is not True:
        return {
            "ok": False,
            "issue": "VENUE_HEARTBEAT_RESTING_ORDER_UNSAFE",
            "health": health,
            "resting_order_safe": resting_order_safe,
            "age_seconds": age,
            "max_age_seconds": max_age,
        }

    return {
        "ok": True,
        "issue": None,
        "health": health,
        "resting_order_safe": resting_order_safe,
        "age_seconds": age,
        "max_age_seconds": max_age,
    }


def _sqlite_ro_scalar(
    path: Path,
    sql: str,
    params: tuple[object, ...] = (),
) -> tuple[object | None, str | None]:
    if not path.exists():
        return None, "DB_MISSING"
    try:
        from src.state.db import _connect_read_only

        conn = _connect_read_only(path)
        try:
            row = conn.execute(sql, params).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"DB_READ_FAILED:{type(exc).__name__}:{exc}"
    if row is None:
        return None, None
    return row[0], None


def _sqlite_ro_rows(
    path: Path,
    sql: str,
    params: tuple[object, ...] = (),
) -> tuple[list[dict], str | None]:
    if not path.exists():
        return [], "DB_MISSING"
    try:
        from src.state.db import _connect_read_only

        conn = _connect_read_only(path)
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return [], f"DB_READ_FAILED:{type(exc).__name__}:{exc}"
    return [dict(row) for row in rows], None


def _sqlite_ro_table_columns(path: Path, table: str) -> tuple[set[str], str | None]:
    if not path.exists():
        return set(), "DB_MISSING"
    try:
        from src.state.db import _connect_read_only

        conn = _connect_read_only(path)
        try:
            table_row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
                (table,),
            ).fetchone()
            if table_row is None:
                return set(), None
            escaped_table = table.replace('"', '""')
            rows = conn.execute(f'PRAGMA table_info("{escaped_table}")').fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return set(), f"DB_READ_FAILED:{type(exc).__name__}:{exc}"
    return {str(row["name"]) for row in rows}, None


def _optional_sql_column(
    alias: str,
    columns: set[str],
    column: str,
    output_column: str | None = None,
) -> str:
    output = output_column or column
    if column in columns:
        return f"{alias}.{column} AS {output}"
    return f"NULL AS {output}"


def _forecast_event_queue_detail(world_db: Path) -> dict:
    """Read-only active FSR/redecision queue context for bridge alerts."""

    event_columns, event_err = _sqlite_ro_table_columns(world_db, "opportunity_events")
    if event_err:
        return {"evaluated": False, "issue": f"FORECAST_EVENT_READ_UNAVAILABLE:{event_err}"}
    processing_columns, processing_err = _sqlite_ro_table_columns(
        world_db,
        "opportunity_event_processing",
    )
    if processing_err:
        return {
            "evaluated": False,
            "issue": f"FORECAST_EVENT_PROCESSING_READ_UNAVAILABLE:{processing_err}",
        }
    required_event = {"event_id", "event_type", "created_at"}
    required_processing = {
        "consumer_name",
        "event_id",
        "processing_status",
        "last_error",
        "updated_at",
    }
    missing_event = sorted(required_event - event_columns)
    missing_processing = sorted(required_processing - processing_columns)
    if missing_event or missing_processing:
        return {
            "evaluated": False,
            "issue": "FORECAST_EVENT_QUEUE_COLUMNS_MISSING",
            "missing_event_columns": missing_event,
            "missing_processing_columns": missing_processing,
        }

    rows, err = _sqlite_ro_rows(
        world_db,
        """
        SELECT
            COUNT(*) AS active_decision_event_count,
            SUM(CASE WHEN e.event_type = 'FORECAST_SNAPSHOT_READY' THEN 1 ELSE 0 END)
                AS active_fsr_count,
            SUM(CASE WHEN e.event_type = 'EDLI_REDECISION_PENDING' THEN 1 ELSE 0 END)
                AS active_redecision_count,
            SUM(
                CASE
                    WHEN e.event_type = 'FORECAST_SNAPSHOT_READY'
                     AND (p.last_error IS NULL OR TRIM(CAST(p.last_error AS TEXT)) = '')
                    THEN 1 ELSE 0
                END
            ) AS active_fsr_blank_error_count,
            SUM(
                CASE
                    WHEN e.event_type IN ('FORECAST_SNAPSHOT_READY', 'EDLI_REDECISION_PENDING')
                     AND p.last_error LIKE 'QKERNEL_ACTUAL_SUBMIT_QUALITY_FLOOR:%'
                    THEN 1 ELSE 0
                END
            ) AS terminal_quality_retry_debt_count,
            MAX(CASE WHEN e.event_type = 'FORECAST_SNAPSHOT_READY' THEN e.created_at ELSE NULL END)
                AS latest_active_fsr_created_at,
            MAX(CASE WHEN e.event_type = 'FORECAST_SNAPSHOT_READY' THEN p.updated_at ELSE NULL END)
                AS latest_active_fsr_processing_updated_at
          FROM opportunity_event_processing p
          JOIN opportunity_events e
            ON e.event_id = p.event_id
         WHERE p.consumer_name = 'edli_reactor_v1'
           AND p.processing_status IN ('pending', 'processing')
           AND e.event_type IN ('FORECAST_SNAPSHOT_READY', 'EDLI_REDECISION_PENDING')
        """,
    )
    if err:
        return {"evaluated": False, "issue": f"FORECAST_EVENT_QUEUE_READ_UNAVAILABLE:{err}"}
    row = rows[0] if rows else {}

    def _int_value(name: str) -> int:
        try:
            return int(row.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    cause_hints: list[str] = []
    if _int_value("terminal_quality_retry_debt_count") > 0:
        cause_hints.append("terminal_quality_retry_debt")
    if _int_value("active_fsr_count") > 0:
        cause_hints.append("active_fsr_backlog")
    if _int_value("active_redecision_count") > 0:
        cause_hints.append("active_redecision_backlog")

    return {
        "evaluated": True,
        "issue": None,
        "active_decision_event_count": _int_value("active_decision_event_count"),
        "active_fsr_count": _int_value("active_fsr_count"),
        "active_redecision_count": _int_value("active_redecision_count"),
        "active_fsr_blank_error_count": _int_value("active_fsr_blank_error_count"),
        "terminal_quality_retry_debt_count": _int_value("terminal_quality_retry_debt_count"),
        "latest_active_fsr_created_at": row.get("latest_active_fsr_created_at"),
        "latest_active_fsr_processing_updated_at": row.get(
            "latest_active_fsr_processing_updated_at"
        ),
        "cause_hints": cause_hints,
    }


def _forecast_to_event_bridge_surface(
    state_dir: Path,
    now: datetime,
    *,
    main_daemon_surface: dict,
) -> dict:
    """Prove live posterior production is reaching the trading event queue.

    Forecast-live materializes ``forecast_posteriors`` while ``src.main`` emits
    ``FORECAST_SNAPSHOT_READY`` opportunity events. A heartbeat on either side
    alone is not business progress. Evaluate this bridge only when the main
    daemon is currently attested; during an intentional stopped/restart-preflight
    state, reporting the gap is useful but must not become a false blocker.
    """

    if not bool(main_daemon_surface.get("attested")):
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_MAIN_DAEMON_NOT_ATTESTED",
            "evaluated": False,
        }

    forecast_db = state_dir / "zeus-forecasts.db"
    world_db = state_dir / "zeus-world.db"
    queue_detail = _forecast_event_queue_detail(world_db)
    latest_posterior, posterior_err = _sqlite_ro_scalar(
        forecast_db,
        """
        SELECT MAX(computed_at)
          FROM forecast_posteriors
         WHERE runtime_layer = 'live'
        """,
    )
    if posterior_err:
        return {
            "ok": False,
            "issue": f"LIVE_POSTERIOR_READ_UNAVAILABLE:{posterior_err}",
            "evaluated": True,
        }
    event_columns, fsr_err = _sqlite_ro_table_columns(world_db, "opportunity_events")
    if fsr_err:
        return {
            "ok": False,
            "issue": f"FORECAST_EVENT_READ_UNAVAILABLE:{fsr_err}",
            "evaluated": True,
        }
    if event_columns and {"event_type", "created_at"}.issubset(event_columns):
        payload_select = (
            "payload_json"
            if "payload_json" in event_columns
            else "NULL AS payload_json"
        )
        latest_fsr_rows, fsr_rows_err = _sqlite_ro_rows(
            world_db,
            f"""
            SELECT event_id, entity_key, created_at, {payload_select}
              FROM opportunity_events
             WHERE event_type = 'FORECAST_SNAPSHOT_READY'
             ORDER BY datetime(created_at) DESC, rowid DESC
             LIMIT 1
            """,
        )
        if fsr_rows_err:
            return {
                "ok": False,
                "issue": f"FORECAST_EVENT_READ_UNAVAILABLE:{fsr_rows_err}",
                "evaluated": True,
            }
    else:
        latest_fsr_rows = []

    posterior_at = _parse_iso_utc(latest_posterior)
    latest_fsr_row = latest_fsr_rows[0] if latest_fsr_rows else {}
    latest_fsr = latest_fsr_row.get("created_at")
    fsr_at = _parse_iso_utc(latest_fsr)
    if posterior_at is None:
        return {
            "ok": False,
            "issue": "LIVE_POSTERIOR_MISSING_OR_UNPARSEABLE",
            "evaluated": True,
            "latest_posterior_computed_at": latest_posterior,
            "latest_fsr_created_at": latest_fsr,
        }

    posterior_age = max(0.0, (now.astimezone(timezone.utc) - posterior_at).total_seconds())
    fsr_age = (
        max(0.0, (now.astimezone(timezone.utc) - fsr_at).total_seconds())
        if fsr_at is not None
        else None
    )
    lag_seconds = (
        (posterior_at - fsr_at).total_seconds()
        if fsr_at is not None
        else float("inf")
    )
    fsr_payload: dict[str, object] = {}
    payload_raw = latest_fsr_row.get("payload_json")
    if isinstance(payload_raw, str) and payload_raw.strip():
        try:
            decoded = json.loads(payload_raw)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            fsr_payload = decoded
    fsr_identity = str(
        fsr_payload.get("source_run_id")
        or fsr_payload.get("snapshot_hash")
        or ""
    ).strip()
    identity_match: dict[str, object] | None = None
    identity_payload_lag_seconds: float | None = None
    identity_to_latest_lag_seconds: float | None = None
    if fsr_identity:
        posterior_columns, posterior_column_err = _sqlite_ro_table_columns(
            forecast_db,
            "forecast_posteriors",
        )
        if posterior_column_err:
            return {
                "ok": False,
                "issue": f"LIVE_POSTERIOR_READ_UNAVAILABLE:{posterior_column_err}",
                "evaluated": True,
            }
        if "posterior_identity_hash" in posterior_columns:
            predicates = ["runtime_layer = 'live'", "posterior_identity_hash = ?"]
            params: list[str] = [fsr_identity]
            fsr_city = str(fsr_payload.get("city") or "").strip()
            fsr_target = str(fsr_payload.get("target_date") or "").strip()
            fsr_metric = str(fsr_payload.get("metric") or "").strip()
            if fsr_city and "city" in posterior_columns:
                predicates.append("city = ?")
                params.append(fsr_city)
            if fsr_target and "target_date" in posterior_columns:
                predicates.append("target_date = ?")
                params.append(fsr_target)
            if fsr_metric and "temperature_metric" in posterior_columns:
                predicates.append("temperature_metric = ?")
                params.append(fsr_metric)
            identity_rows, identity_err = _sqlite_ro_rows(
                forecast_db,
                f"""
                SELECT computed_at,
                       source_cycle_time,
                       source_available_at,
                       posterior_identity_hash
                  FROM forecast_posteriors
                 WHERE {" AND ".join(predicates)}
                 ORDER BY datetime(computed_at) DESC
                 LIMIT 1
                """,
                tuple(params),
            )
            if identity_err:
                return {
                    "ok": False,
                    "issue": f"LIVE_POSTERIOR_READ_UNAVAILABLE:{identity_err}",
                    "evaluated": True,
                }
            if identity_rows:
                identity_match = identity_rows[0]
                payload_available_at = _parse_iso_utc(
                    fsr_payload.get("available_at") or fsr_payload.get("captured_at")
                )
                identity_computed_at = _parse_iso_utc(
                    identity_match.get("computed_at")
                )
                if identity_computed_at is not None:
                    identity_to_latest_lag_seconds = (
                        posterior_at - identity_computed_at
                    ).total_seconds()
                if payload_available_at is not None and identity_computed_at is not None:
                    identity_payload_lag_seconds = (
                        payload_available_at - identity_computed_at
                    ).total_seconds()
    detail = {
        "evaluated": True,
        "latest_posterior_computed_at": posterior_at.isoformat(),
        "latest_fsr_created_at": fsr_at.isoformat() if fsr_at is not None else None,
        "posterior_age_seconds": posterior_age,
        "fsr_age_seconds": fsr_age,
        "posterior_to_fsr_lag_seconds": lag_seconds,
        "latest_fsr_identity": fsr_identity or None,
        "latest_fsr_payload_available_at": (
            fsr_payload.get("available_at") or fsr_payload.get("captured_at")
        )
        if fsr_payload
        else None,
        "latest_fsr_identity_match": identity_match,
        "latest_fsr_identity_payload_lag_seconds": identity_payload_lag_seconds,
        "latest_fsr_identity_to_latest_posterior_lag_seconds": (
            identity_to_latest_lag_seconds
        ),
        "event_queue": queue_detail,
        "max_lag_seconds": FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS,
    }
    if identity_match is not None:
        if (
            identity_to_latest_lag_seconds is not None
            and identity_to_latest_lag_seconds > FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS
        ):
            return {
                "ok": False,
                "issue": (
                    "FORECAST_EVENT_POSTERIOR_IDENTITY_SUPERSEDED:"
                    f"latest_newer_by={identity_to_latest_lag_seconds:.0f}s"
                ),
                **detail,
            }
        if (
            identity_payload_lag_seconds is not None
            and identity_payload_lag_seconds > FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS
        ):
            return {
                "ok": False,
                "issue": (
                    "FORECAST_EVENT_POSTERIOR_IDENTITY_STALE:"
                    f"payload_newer_by={identity_payload_lag_seconds:.0f}s"
                ),
                **detail,
            }
        return {
            "ok": True,
            "issue": None,
            "bridge_mode": "fsr_identity_match",
            **detail,
        }
    if (
        lag_seconds > FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS
        and posterior_age > FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS
    ):
        return {
            "ok": False,
            "issue": (
                "FORECAST_TO_EVENT_BRIDGE_STALLED:"
                f"posterior_newer_by={lag_seconds:.0f}s"
            ),
            **detail,
        }
    if posterior_age > FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS:
        return {
            "ok": False,
            "issue": f"LIVE_POSTERIOR_STALE:age={posterior_age:.0f}s",
            **detail,
        }
    return {"ok": True, "issue": None, **detail}


def _entry_q_version_reconstruction_sample(
    trade_db: Path,
    active_sample: list[dict],
) -> list[dict]:
    """Attach certificate evidence for active legacy entries missing q_version."""

    if not active_sample:
        return []

    certificate_columns, column_err = _sqlite_ro_table_columns(
        trade_db,
        "decision_certificates",
    )
    if column_err:
        return [
            _entry_q_version_reconstruction_status(
                row,
                f"decision_certificates_unreadable:{column_err}",
            )
            for row in active_sample
        ]
    if not certificate_columns:
        return [
            _entry_q_version_reconstruction_status(
                row,
                "decision_certificates_table_missing",
            )
            for row in active_sample
        ]
    required_columns = {"certificate_type", "payload_json"}
    missing_columns = sorted(required_columns - certificate_columns)
    if missing_columns:
        return [
            _entry_q_version_reconstruction_status(
                row,
                "decision_certificates_column_missing:" + ",".join(missing_columns),
            )
            for row in active_sample
        ]
    edge_columns, edge_column_err = _sqlite_ro_table_columns(
        trade_db,
        "decision_certificate_edges",
    )
    edge_chain_available = (
        edge_column_err is None
        and {"child_certificate_id", "parent_role", "parent_certificate_hash"}
        <= edge_columns
        and {"certificate_id", "certificate_hash", "semantic_key"} <= certificate_columns
    )

    def cert_column(column: str) -> str:
        if column in certificate_columns:
            return column
        return f"NULL AS {column}"

    select_columns = [
        cert_column("certificate_id"),
        cert_column("certificate_hash"),
        cert_column("decision_time"),
        cert_column("created_at"),
        "payload_json",
    ]
    order_by = (
        "datetime(created_at) DESC"
        if "created_at" in certificate_columns
        else "rowid DESC"
    )

    try:
        from src.state.db import _connect_read_only  # noqa: PLC0415

        conn = _connect_read_only(trade_db)
    except Exception as exc:  # noqa: BLE001
        return [
            _entry_q_version_reconstruction_status(
                row,
                f"decision_certificates_unreadable:{type(exc).__name__}:{exc}",
            )
            for row in active_sample
        ]

    try:
        reconstructed: list[dict] = []
        for row in active_sample:
            if edge_chain_available:
                edge_reconstruction = _entry_q_version_reconstruction_from_edge(
                    conn,
                    row,
                    certificate_columns=certificate_columns,
                )
                if edge_reconstruction is not None:
                    reconstructed.append(edge_reconstruction)
                    continue

            target_ids = _entry_q_version_reconstruction_target_ids(row)
            if not target_ids:
                reconstructed.append(
                    _entry_q_version_reconstruction_status(row, "snapshot_id_missing")
                )
                continue

            where_clause = " OR ".join(["instr(payload_json, ?) > 0"] * len(target_ids))
            rows = conn.execute(
                f"""
                SELECT {", ".join(select_columns)}
                  FROM decision_certificates
                 WHERE certificate_type = 'FinalIntentCertificate'
                   AND ({where_clause})
                 ORDER BY {order_by}
                 LIMIT 5
                """,
                tuple(target_ids),
            ).fetchall()
            exact_matches: list[dict] = []
            parse_failures = 0
            for cert_row in rows:
                cert = dict(cert_row)
                try:
                    payload = json.loads(str(cert.get("payload_json") or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    parse_failures += 1
                    continue
                if not isinstance(payload, dict):
                    parse_failures += 1
                    continue
                if _entry_q_version_payload_matches(payload, target_ids):
                    exact_matches.append(
                        _entry_q_version_reconstruction_from_payload(row, cert, payload)
                    )

            if len(exact_matches) == 1:
                reconstructed.append(exact_matches[0])
            elif len(exact_matches) > 1:
                candidate = exact_matches[0]
                candidate["reconstruction_status"] = "ambiguous_final_intent_certificate"
                candidate["matching_certificate_count"] = len(exact_matches)
                reconstructed.append(candidate)
            elif rows:
                status = (
                    "final_intent_certificate_payload_parse_failed"
                    if parse_failures
                    else "final_intent_certificate_not_exact"
                )
                item = _entry_q_version_reconstruction_status(row, status)
                item["candidate_certificate_count"] = len(rows)
                reconstructed.append(item)
            else:
                reconstructed.append(
                    _entry_q_version_reconstruction_status(
                        row,
                        "final_intent_certificate_not_found",
                    )
                )
    finally:
        conn.close()
    return reconstructed


def _entry_q_version_reconstruction_status(row: dict, status: str) -> dict:
    return {
        "position_id": row.get("position_id"),
        "command_id": row.get("command_id"),
        "decision_id": row.get("decision_id"),
        "snapshot_id": row.get("snapshot_id"),
        "decision_snapshot_id": row.get("decision_snapshot_id"),
        "reconstruction_status": status,
    }


def _entry_q_version_reconstruction_from_edge(
    conn,
    row: dict,
    *,
    certificate_columns: set[str],
) -> dict | None:
    decision_id = str(row.get("decision_id") or "").strip()
    if not decision_id:
        return None

    def final_column(column: str) -> str:
        if column in certificate_columns:
            return f"fc.{column} AS {column}"
        return f"NULL AS {column}"

    select_columns = [
        final_column("certificate_id"),
        final_column("certificate_hash"),
        final_column("decision_time"),
        final_column("created_at"),
        "fc.payload_json AS payload_json",
        "ec.certificate_id AS execution_certificate_id",
        "ec.certificate_hash AS execution_certificate_hash",
    ]
    order_by = (
        "datetime(ec.created_at) DESC, ec.certificate_id DESC"
        if "created_at" in certificate_columns
        else "ec.certificate_id DESC"
    )
    rows = conn.execute(
        f"""
        SELECT {", ".join(select_columns)}
          FROM decision_certificates ec
          JOIN decision_certificate_edges edge
            ON edge.child_certificate_id = ec.certificate_id
          JOIN decision_certificates fc
            ON fc.certificate_hash = edge.parent_certificate_hash
         WHERE ec.certificate_type = 'ExecutionCommandCertificate'
           AND instr(ec.semantic_key, ?) > 0
           AND edge.parent_role = 'final_intent'
           AND fc.certificate_type = 'FinalIntentCertificate'
         ORDER BY {order_by}
         LIMIT 5
        """,
        (decision_id,),
    ).fetchall()
    if not rows:
        return None

    parse_failures = 0
    parsed: list[dict] = []
    for cert_row in rows:
        cert = dict(cert_row)
        try:
            payload = json.loads(str(cert.get("payload_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            parse_failures += 1
            continue
        if not isinstance(payload, dict):
            parse_failures += 1
            continue
        item = _entry_q_version_reconstruction_from_payload(row, cert, payload)
        item["reconstruction_status"] = "reconstructed_from_final_intent_edge"
        item["execution_certificate_id"] = cert.get("execution_certificate_id")
        item["execution_certificate_hash"] = cert.get("execution_certificate_hash")
        parsed.append(item)

    if parsed:
        item = parsed[0]
        item["matching_execution_certificate_count"] = len(parsed)
        return item
    status = "final_intent_edge_payload_parse_failed" if parse_failures else "final_intent_edge_empty"
    item = _entry_q_version_reconstruction_status(row, status)
    item["matching_execution_certificate_count"] = len(rows)
    return item


def _entry_q_version_reconstruction_target_ids(row: dict) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for key in ("snapshot_id", "decision_snapshot_id"):
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            ids.append(text)
    return ids


def _entry_q_version_payload_matches(payload: dict, target_ids: list[str]) -> bool:
    context = payload.get("decision_source_context")
    if not isinstance(context, dict):
        context = {}
    payload_ids = {
        str(payload.get("executable_snapshot_id") or "").strip(),
        str(context.get("snapshot_id") or "").strip(),
    }
    return any(target_id in payload_ids for target_id in target_ids)


def _entry_q_version_reconstruction_from_payload(
    row: dict,
    cert: dict,
    payload: dict,
) -> dict:
    context = payload.get("decision_source_context")
    if not isinstance(context, dict):
        context = {}
    item = _entry_q_version_reconstruction_status(
        row,
        "reconstructed_from_final_intent_certificate",
    )
    item.update(
        {
            "certificate_id": cert.get("certificate_id"),
            "certificate_hash": cert.get("certificate_hash"),
            "certificate_decision_time": cert.get("decision_time"),
            "certificate_created_at": cert.get("created_at"),
            "executable_snapshot_id": payload.get("executable_snapshot_id"),
            "posterior_identity_hash": context.get("posterior_identity_hash"),
            "q_live": payload.get("q_live"),
            "q_lcb_5pct": payload.get("q_lcb_5pct"),
            "selection_authority_applied": payload.get("selection_authority_applied"),
            "forecast_source_id": context.get("forecast_source_id"),
            "source_available_at": context.get("source_available_at"),
            "forecast_available_at": context.get("forecast_available_at"),
        }
    )
    return item


def _entry_q_version_surface(
    state_dir: Path,
    now: datetime,
    *,
    main_daemon_surface: dict,
) -> dict:
    """Prove recent entry commands are linkable to the q that authorized them."""

    if not bool(main_daemon_surface.get("attested")):
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_MAIN_DAEMON_NOT_ATTESTED",
            "evaluated": False,
        }

    trade_db = state_dir / "zeus_trades.db"
    columns, column_err = _sqlite_ro_table_columns(trade_db, "venue_commands")
    if column_err:
        return {
            "ok": False,
            "issue": f"ENTRY_Q_VERSION_READ_UNAVAILABLE:{column_err}",
            "evaluated": True,
        }
    if not columns:
        return {
            "ok": False,
            "issue": "ENTRY_Q_VERSION_TABLE_MISSING",
            "evaluated": True,
        }
    required_columns = {"command_id", "intent_kind", "state", "created_at", "q_version"}
    missing_columns = sorted(required_columns - columns)
    if missing_columns:
        return {
            "ok": False,
            "issue": "ENTRY_Q_VERSION_COLUMN_MISSING:" + ",".join(missing_columns),
            "evaluated": True,
            "missing_columns": missing_columns,
        }

    lookback_cutoff = (
        now.astimezone(timezone.utc)
        - timedelta(seconds=ENTRY_Q_VERSION_LOOKBACK_SECONDS)
    )
    loaded_payload = _read_json(state_dir / "loaded_sha.json") or {}
    boot_generated_at = _parse_iso_utc(loaded_payload.get("generated_at"))
    boot_cutoff_used = False
    cutoff_at = lookback_cutoff
    if (
        boot_generated_at is not None
        and boot_generated_at <= now.astimezone(timezone.utc) + timedelta(seconds=5)
        and boot_generated_at > cutoff_at
    ):
        cutoff_at = boot_generated_at
        boot_cutoff_used = True
    cutoff = cutoff_at.isoformat()
    missing_count, count_err = _sqlite_ro_scalar(
        trade_db,
        """
        SELECT COUNT(*)
          FROM venue_commands
         WHERE intent_kind = 'ENTRY'
           AND created_at >= ?
           AND (q_version IS NULL OR TRIM(CAST(q_version AS TEXT)) = '')
        """,
        (cutoff,),
    )
    if count_err:
        return {
            "ok": False,
            "issue": f"ENTRY_Q_VERSION_READ_UNAVAILABLE:{count_err}",
            "evaluated": True,
        }
    try:
        missing_total = int(missing_count or 0)
    except (TypeError, ValueError):
        missing_total = 0

    sample, sample_err = _sqlite_ro_rows(
        trade_db,
        """
        SELECT command_id, state, created_at
          FROM venue_commands
         WHERE intent_kind = 'ENTRY'
           AND created_at >= ?
           AND (q_version IS NULL OR TRIM(CAST(q_version AS TEXT)) = '')
         ORDER BY created_at DESC
         LIMIT ?
        """,
        (cutoff, ENTRY_Q_VERSION_SAMPLE_LIMIT),
    )
    if sample_err:
        return {
            "ok": False,
            "issue": f"ENTRY_Q_VERSION_READ_UNAVAILABLE:{sample_err}",
            "evaluated": True,
        }

    active_missing_total = 0
    active_sample: list[dict] = []
    active_reconstruction_sample: list[dict] = []
    active_exposure_evaluated = False
    active_exposure_skip_reason: str | None = None
    position_columns, position_column_err = _sqlite_ro_table_columns(trade_db, "position_current")
    if position_column_err:
        active_exposure_skip_reason = position_column_err
    elif "position_id" not in columns:
        active_exposure_skip_reason = "VENUE_COMMANDS_POSITION_ID_COLUMN_MISSING"
    elif not position_columns:
        active_exposure_skip_reason = "POSITION_CURRENT_TABLE_MISSING"
    else:
        required_position_columns = {
            "position_id",
            "phase",
            "order_status",
            "shares",
            "chain_shares",
        }
        missing_position_columns = sorted(required_position_columns - position_columns)
        if missing_position_columns:
            active_exposure_skip_reason = (
                "POSITION_CURRENT_COLUMN_MISSING:" + ",".join(missing_position_columns)
            )
        else:
            active_exposure_evaluated = True
            active_missing_count, active_count_err = _sqlite_ro_scalar(
                trade_db,
                """
                SELECT COUNT(DISTINCT pc.position_id)
                  FROM position_current pc
                  JOIN venue_commands vc
                    ON vc.position_id = pc.position_id
                 WHERE vc.intent_kind = 'ENTRY'
                   AND pc.phase IN ('active', 'day0_window', 'pending_exit')
                   AND (
                       COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                       OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
                   )
                   AND (vc.q_version IS NULL OR TRIM(CAST(vc.q_version AS TEXT)) = '')
                """,
            )
            if active_count_err:
                return {
                    "ok": False,
                    "issue": f"ENTRY_Q_VERSION_READ_UNAVAILABLE:{active_count_err}",
                    "evaluated": True,
                }
            try:
                active_missing_total = int(active_missing_count or 0)
            except (TypeError, ValueError):
                active_missing_total = 0
            active_select_columns = [
                "pc.position_id",
                "pc.phase",
                "pc.order_status",
                "pc.shares",
                "pc.chain_shares",
                _optional_sql_column("pc", position_columns, "decision_snapshot_id"),
                _optional_sql_column("pc", position_columns, "city"),
                _optional_sql_column("pc", position_columns, "target_date"),
                _optional_sql_column("pc", position_columns, "bin_label"),
                _optional_sql_column("pc", position_columns, "direction"),
                _optional_sql_column("pc", position_columns, "p_posterior"),
                "vc.command_id",
                "vc.state",
                "vc.created_at",
                _optional_sql_column("vc", columns, "decision_id"),
                _optional_sql_column("vc", columns, "snapshot_id"),
                _optional_sql_column("vc", columns, "price"),
                _optional_sql_column("vc", columns, "size"),
            ]
            active_sample_sql = f"""
                SELECT {", ".join(active_select_columns)}
                  FROM position_current pc
                  JOIN venue_commands vc
                    ON vc.position_id = pc.position_id
                 WHERE vc.intent_kind = 'ENTRY'
                   AND pc.phase IN ('active', 'day0_window', 'pending_exit')
                   AND (
                       COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                       OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
                   )
                   AND (vc.q_version IS NULL OR TRIM(CAST(vc.q_version AS TEXT)) = '')
                 ORDER BY datetime(vc.created_at) DESC, vc.command_id DESC
                 LIMIT ?
            """
            active_sample, active_sample_err = _sqlite_ro_rows(
                trade_db,
                active_sample_sql,
                (ENTRY_Q_VERSION_SAMPLE_LIMIT,),
            )
            if active_sample_err:
                return {
                    "ok": False,
                    "issue": f"ENTRY_Q_VERSION_READ_UNAVAILABLE:{active_sample_err}",
                    "evaluated": True,
                }
            active_reconstruction_sample = _entry_q_version_reconstruction_sample(
                trade_db,
                active_sample,
            )

    detail = {
        "evaluated": True,
        "lookback_seconds": ENTRY_Q_VERSION_LOOKBACK_SECONDS,
        "cutoff_at": cutoff,
        "boot_generated_at": (
            boot_generated_at.isoformat() if boot_generated_at is not None else None
        ),
        "boot_cutoff_used": boot_cutoff_used,
        "missing_q_version_count": missing_total,
        "missing_q_version_sample": sample,
        "active_exposure_evaluated": active_exposure_evaluated,
        "active_exposure_skip_reason": active_exposure_skip_reason,
        "active_missing_q_version_count": active_missing_total,
        "active_missing_q_version_sample": active_sample,
        "active_missing_q_version_reconstruction_sample": active_reconstruction_sample,
    }
    if active_missing_total > 0:
        return {
            "ok": False,
            "issue": f"ENTRY_Q_VERSION_MISSING_ACTIVE_EXPOSURE:n={active_missing_total}",
            **detail,
        }
    if missing_total > 0:
        return {
            "ok": False,
            "issue": f"ENTRY_Q_VERSION_MISSING:n={missing_total}",
            **detail,
        }
    return {"ok": True, "issue": None, **detail}


def _status_summary_order_state_conflict_surface(
    status_summary: Optional[dict],
    *,
    state_dir: Path,
) -> dict:
    """Surface fresh status files that still report ambiguous order truth."""

    if not isinstance(status_summary, dict):
        return {"ok": True, "issue": None, "evaluated": False}
    execution = status_summary.get("execution")
    if not isinstance(execution, dict):
        return {
            "ok": True,
            "issue": None,
            "evaluated": True,
            "terminal_command_venue_fact_conflict_count": 0,
            "terminal_command_venue_fact_conflict_sample": [],
        }
    conflicts = execution.get("terminal_command_venue_fact_conflicts")
    if not isinstance(conflicts, dict):
        return {
            "ok": True,
            "issue": None,
            "evaluated": True,
            "terminal_command_venue_fact_conflict_count": 0,
            "terminal_command_venue_fact_conflict_sample": [],
        }
    try:
        conflict_count = int(conflicts.get("count") or 0)
    except (TypeError, ValueError):
        conflict_count = 0
    orders = conflicts.get("orders")
    sample = orders[:5] if isinstance(orders, list) else []
    recomputed_conflicts: dict[str, object] | None = None
    if conflict_count > 0:
        try:
            from src.observability.status_summary import (  # noqa: PLC0415
                _query_terminal_entry_command_venue_fact_conflicts,
            )
            from src.state.db import _connect_read_only  # noqa: PLC0415

            trade_db = state_dir / "zeus_trades.db"
            conn = _connect_read_only(trade_db)
            try:
                recomputed_conflicts = _query_terminal_entry_command_venue_fact_conflicts(
                    conn
                )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - keep the status_summary fallback visible
            recomputed_conflicts = {
                "status": "recompute_error",
                "error": f"{exc.__class__.__name__}:{exc}",
            }
    effective_conflicts = (
        recomputed_conflicts
        if isinstance(recomputed_conflicts, dict)
        and recomputed_conflicts.get("status") == "ok"
        else conflicts
    )
    effective_orders = effective_conflicts.get("orders")
    effective_order_list = effective_orders if isinstance(effective_orders, list) else sample
    current_conflicts = [
        row for row in effective_order_list
        if _terminal_command_venue_fact_conflict_is_current(row)
    ]
    historical_conflicts = [
        row for row in effective_order_list
        if not _terminal_command_venue_fact_conflict_is_current(row)
    ]
    effective_count = len(current_conflicts)
    total_count = len(effective_order_list)
    detail = {
        "evaluated": True,
        "terminal_command_venue_fact_conflict_count": effective_count,
        "terminal_command_venue_fact_conflict_sample": current_conflicts[:5],
        "terminal_command_venue_fact_conflict_total_count": total_count,
        "terminal_command_venue_fact_conflict_historical_count": len(historical_conflicts),
        "terminal_command_venue_fact_conflict_historical_sample": historical_conflicts[:5],
        "status_summary_terminal_command_venue_fact_conflict_count": conflict_count,
        "status_summary_terminal_command_venue_fact_conflict_sample": sample,
        "terminal_command_venue_fact_conflict_recomputed": recomputed_conflicts,
    }
    if effective_count > 0:
        return {
            "ok": False,
            "issue": f"TERMINAL_COMMAND_VENUE_FACT_CONFLICT:n={effective_count}",
            **detail,
        }
    return {"ok": True, "issue": None, **detail}


def _terminal_command_venue_fact_conflict_is_current(row: object) -> bool:
    if not isinstance(row, dict):
        return True
    phase = str(row.get("phase") or "").strip().lower()
    if phase in {"economically_closed", "settled", "voided", "admin_closed"}:
        return False
    return True


def _pending_exit_release_loop_surface(
    state_dir: Path,
    now: datetime,
    *,
    main_daemon_surface: dict,
) -> dict:
    """Detect pending-exit rows repeatedly released back into held monitoring."""

    if not bool(main_daemon_surface.get("attested")):
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_MAIN_DAEMON_NOT_ATTESTED",
            "evaluated": False,
        }

    trade_db = state_dir / "zeus_trades.db"
    event_columns, event_column_err = _sqlite_ro_table_columns(trade_db, "position_events")
    if event_column_err:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_RELEASE_LOOP_READ_UNAVAILABLE:{event_column_err}",
            "evaluated": True,
        }
    if not event_columns:
        return {
            "ok": True,
            "issue": None,
            "evaluated": False,
            "skip_reason": "POSITION_EVENTS_TABLE_MISSING",
        }
    required_event_columns = {"position_id", "sequence_no", "event_type", "occurred_at"}
    missing_event_columns = sorted(required_event_columns - event_columns)
    if missing_event_columns:
        return {
            "ok": False,
            "issue": "PENDING_EXIT_RELEASE_LOOP_COLUMN_MISSING:"
            + ",".join(missing_event_columns),
            "evaluated": True,
            "missing_columns": missing_event_columns,
        }

    position_columns, position_column_err = _sqlite_ro_table_columns(trade_db, "position_current")
    if position_column_err:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_RELEASE_LOOP_READ_UNAVAILABLE:{position_column_err}",
            "evaluated": True,
        }
    if not position_columns:
        return {
            "ok": True,
            "issue": None,
            "evaluated": False,
            "skip_reason": "POSITION_CURRENT_TABLE_MISSING",
        }
    required_position_columns = {
        "position_id",
        "phase",
        "order_status",
        "shares",
        "chain_shares",
    }
    missing_position_columns = sorted(required_position_columns - position_columns)
    if missing_position_columns:
        return {
            "ok": False,
            "issue": "PENDING_EXIT_RELEASE_LOOP_COLUMN_MISSING:"
            + ",".join(missing_position_columns),
            "evaluated": True,
            "missing_columns": missing_position_columns,
        }

    optional_position_columns = (
        "city",
        "target_date",
        "bin_label",
        "direction",
        "exit_reason",
        "updated_at",
    )
    optional_position_select = ",\n               ".join(
        (
            f"pc.{column}"
            if column in position_columns
            else f"NULL AS {column}"
        )
        for column in optional_position_columns
    )
    no_command_order_by = (
        "datetime(pc.updated_at) DESC, pc.position_id"
        if "updated_at" in position_columns
        else "pc.position_id"
    )

    no_command_sample: list[dict] = []
    no_command_skip_reason: str | None = None
    command_columns, command_column_err = _sqlite_ro_table_columns(
        trade_db,
        "venue_commands",
    )
    if command_column_err:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_RELEASE_LOOP_READ_UNAVAILABLE:{command_column_err}",
            "evaluated": True,
        }
    if not command_columns:
        no_command_skip_reason = "VENUE_COMMANDS_TABLE_MISSING"
    else:
        required_command_columns = {"position_id", "intent_kind"}
        missing_command_columns = sorted(required_command_columns - command_columns)
        if missing_command_columns:
            no_command_skip_reason = (
                "VENUE_COMMANDS_COLUMN_MISSING:" + ",".join(missing_command_columns)
            )
        else:
            no_command_sample, no_command_err = _sqlite_ro_rows(
                trade_db,
                f"""
                SELECT pc.position_id,
                       pc.phase,
                       pc.order_status,
                       pc.shares,
                       pc.chain_shares,
                       {optional_position_select}
                  FROM position_current pc
                 WHERE pc.phase = 'pending_exit'
                   AND pc.order_status = 'exit_intent'
                   AND (
                       COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                       OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM venue_commands vc
                        WHERE vc.position_id = pc.position_id
                          AND vc.intent_kind = 'EXIT'
                   )
                 ORDER BY {no_command_order_by}
                 LIMIT ?
                """,
                (PENDING_EXIT_RELEASE_LOOP_SAMPLE_LIMIT,),
            )
            if no_command_err:
                return {
                    "ok": False,
                    "issue": f"PENDING_EXIT_RELEASE_LOOP_READ_UNAVAILABLE:{no_command_err}",
                    "evaluated": True,
                }

    cutoff = (
        now.astimezone(timezone.utc)
        - timedelta(seconds=PENDING_EXIT_RELEASE_LOOP_LOOKBACK_SECONDS)
    ).isoformat()
    sample, sample_err = _sqlite_ro_rows(
        trade_db,
        """
        WITH recent_releases AS (
            SELECT position_id,
                   COUNT(*) AS release_count,
                   MIN(occurred_at) AS first_release_at,
                   MAX(occurred_at) AS latest_release_at,
                   MAX(sequence_no) AS latest_release_sequence
              FROM position_events
             WHERE event_type = 'EXIT_RETRY_RELEASED'
               AND datetime(occurred_at) >= datetime(?)
             GROUP BY position_id
            HAVING COUNT(*) >= 2
        ),
        post_release_intents AS (
            SELECT rr.position_id,
                   COUNT(*) AS post_release_exit_intent_count,
                   MAX(e.occurred_at) AS latest_exit_intent_at
              FROM recent_releases rr
              JOIN position_events e
                ON e.position_id = rr.position_id
               AND e.sequence_no > rr.latest_release_sequence
             WHERE e.event_type = 'EXIT_INTENT'
             GROUP BY rr.position_id
        )
        SELECT pc.position_id,
               pc.phase,
               pc.order_status,
               pc.shares,
               pc.chain_shares,
               rr.release_count,
               rr.first_release_at,
               rr.latest_release_at,
               COALESCE(pri.post_release_exit_intent_count, 0)
                   AS post_release_exit_intent_count,
               pri.latest_exit_intent_at,
               pc.city,
               pc.target_date,
               pc.bin_label,
               pc.direction,
               pc.exit_reason
          FROM recent_releases rr
          JOIN position_current pc
            ON pc.position_id = rr.position_id
          LEFT JOIN post_release_intents pri
            ON pri.position_id = rr.position_id
         WHERE pc.phase = 'pending_exit'
           AND pc.order_status IN (
               'exit_intent',
               'retry_pending',
               'backoff_exhausted',
               'sell_pending',
               'sell_placed'
           )
           AND (
               COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
               OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
           )
         ORDER BY rr.release_count DESC, rr.latest_release_at DESC, pc.position_id
         LIMIT ?
        """,
        (cutoff, PENDING_EXIT_RELEASE_LOOP_SAMPLE_LIMIT),
    )
    if sample_err:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_RELEASE_LOOP_READ_UNAVAILABLE:{sample_err}",
            "evaluated": True,
        }

    reassert_sample, reassert_err = _sqlite_ro_rows(
        trade_db,
        """
        WITH recent_reasserts AS (
            SELECT position_id,
                   SUM(
                       CASE
                           WHEN event_type = 'EXIT_INTENT'
                            AND COALESCE(phase_before, '') = 'day0_window'
                            AND COALESCE(phase_after, '') = 'pending_exit'
                           THEN 1 ELSE 0
                       END
                   ) AS reassert_exit_intent_count,
                   SUM(CASE WHEN event_type = 'EXIT_ORDER_REJECTED' THEN 1 ELSE 0 END)
                       AS exit_rejection_count,
                   SUM(
                       CASE
                           WHEN event_type = 'MONITOR_REFRESHED'
                            AND COALESCE(phase_before, '') = 'day0_window'
                            AND COALESCE(phase_after, '') = 'day0_window'
                           THEN 1 ELSE 0
                       END
                   ) AS held_refresh_count,
                   MIN(
                       CASE
                           WHEN event_type = 'EXIT_INTENT'
                            AND COALESCE(phase_before, '') = 'day0_window'
                            AND COALESCE(phase_after, '') = 'pending_exit'
                           THEN occurred_at ELSE NULL
                       END
                   ) AS first_reassert_at,
                   MAX(
                       CASE
                           WHEN event_type = 'EXIT_INTENT'
                            AND COALESCE(phase_before, '') = 'day0_window'
                            AND COALESCE(phase_after, '') = 'pending_exit'
                           THEN occurred_at ELSE NULL
                       END
                   ) AS latest_reassert_at,
                   MAX(
                       CASE
                           WHEN event_type = 'MONITOR_REFRESHED'
                            AND COALESCE(phase_before, '') = 'day0_window'
                            AND COALESCE(phase_after, '') = 'day0_window'
                           THEN occurred_at ELSE NULL
                       END
                   ) AS latest_held_refresh_at
              FROM position_events
             WHERE datetime(occurred_at) >= datetime(?)
             GROUP BY position_id
            HAVING reassert_exit_intent_count >= ?
        )
        SELECT pc.position_id,
               pc.phase,
               pc.order_status,
               pc.shares,
               pc.chain_shares,
               rr.reassert_exit_intent_count,
               rr.exit_rejection_count,
               rr.held_refresh_count,
               rr.first_reassert_at,
               rr.latest_reassert_at,
               rr.latest_held_refresh_at,
               pc.city,
               pc.target_date,
               pc.bin_label,
               pc.direction,
               pc.exit_reason
          FROM recent_reasserts rr
          JOIN position_current pc
            ON pc.position_id = rr.position_id
         WHERE pc.phase IN ('active', 'day0_window')
           AND pc.order_status IN ('filled', 'partial')
           AND (
               COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
               OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
           )
         ORDER BY rr.reassert_exit_intent_count DESC,
                  rr.latest_reassert_at DESC,
                  pc.position_id
         LIMIT ?
        """,
        (
            cutoff,
            PENDING_EXIT_REASSERT_LOOP_MIN_INTENTS,
            PENDING_EXIT_RELEASE_LOOP_SAMPLE_LIMIT,
        ),
    )
    if reassert_err:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_RELEASE_LOOP_READ_UNAVAILABLE:{reassert_err}",
            "evaluated": True,
        }

    churn_cutoff = (
        now.astimezone(timezone.utc)
        - timedelta(seconds=PENDING_EXIT_CHURN_LOOKBACK_SECONDS)
    ).isoformat()
    churn_sample, churn_err = _sqlite_ro_rows(
        trade_db,
        """
        WITH recent_churn AS (
            SELECT position_id,
                   SUM(CASE WHEN event_type = 'EXIT_INTENT' THEN 1 ELSE 0 END)
                       AS exit_intent_count,
                   SUM(CASE WHEN event_type = 'EXIT_ORDER_REJECTED' THEN 1 ELSE 0 END)
                       AS exit_rejection_count,
                   SUM(CASE WHEN event_type = 'EXIT_RETRY_RELEASED' THEN 1 ELSE 0 END)
                       AS exit_release_count,
                   SUM(CASE WHEN event_type = 'MONITOR_REFRESHED' THEN 1 ELSE 0 END)
                       AS monitor_refresh_count,
                   MIN(CASE WHEN event_type = 'EXIT_INTENT' THEN occurred_at ELSE NULL END)
                       AS first_exit_intent_at,
                   MAX(CASE WHEN event_type = 'EXIT_INTENT' THEN occurred_at ELSE NULL END)
                       AS latest_exit_intent_at,
                   MAX(CASE WHEN event_type = 'EXIT_ORDER_REJECTED' THEN occurred_at ELSE NULL END)
                       AS latest_exit_rejection_at,
                   MAX(CASE WHEN event_type = 'EXIT_RETRY_RELEASED' THEN occurred_at ELSE NULL END)
                       AS latest_exit_release_at
              FROM position_events
             WHERE datetime(occurred_at) >= datetime(?)
               AND event_type IN (
                   'EXIT_INTENT',
                   'EXIT_ORDER_REJECTED',
                   'EXIT_RETRY_RELEASED',
                   'MONITOR_REFRESHED'
               )
             GROUP BY position_id
            HAVING exit_intent_count >= ?
               AND (
                   exit_rejection_count >= ?
                   OR exit_release_count >= ?
               )
        )
        SELECT pc.position_id,
               pc.phase,
               pc.order_status,
               pc.shares,
               pc.chain_shares,
               rc.exit_intent_count,
               rc.exit_rejection_count,
               rc.exit_release_count,
               rc.monitor_refresh_count,
               rc.first_exit_intent_at,
               rc.latest_exit_intent_at,
               rc.latest_exit_rejection_at,
               rc.latest_exit_release_at,
               pc.city,
               pc.target_date,
               pc.bin_label,
               pc.direction,
               pc.exit_reason
          FROM recent_churn rc
          JOIN position_current pc
            ON pc.position_id = rc.position_id
         WHERE pc.phase IN ('active', 'day0_window', 'pending_exit')
           AND pc.order_status IN (
               'filled',
               'partial',
               'exit_intent',
               'retry_pending',
               'backoff_exhausted',
               'sell_pending',
               'sell_placed'
           )
           AND (
               COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
               OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
           )
         ORDER BY rc.exit_intent_count DESC,
                  rc.exit_rejection_count DESC,
                  rc.exit_release_count DESC,
                  rc.latest_exit_intent_at DESC,
                  pc.position_id
         LIMIT ?
        """,
        (
            churn_cutoff,
            PENDING_EXIT_CHURN_MIN_INTENTS,
            PENDING_EXIT_CHURN_MIN_REJECTIONS_OR_RELEASES,
            PENDING_EXIT_CHURN_MIN_REJECTIONS_OR_RELEASES,
            PENDING_EXIT_RELEASE_LOOP_SAMPLE_LIMIT,
        ),
    )
    if churn_err:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_RELEASE_LOOP_READ_UNAVAILABLE:{churn_err}",
            "evaluated": True,
        }

    active_churn_sample: list[dict] = []
    historical_churn_sample: list[dict] = []
    for row in churn_sample:
        if _pending_exit_churn_is_current(row, now):
            active_churn_sample.append(row)
        else:
            historical_churn_sample.append(row)

    loop_count = len(sample)
    reassert_count = len(reassert_sample)
    active_churn_count = len(active_churn_sample)
    no_command_count = len(no_command_sample)
    detail = {
        "evaluated": True,
        "lookback_seconds": PENDING_EXIT_RELEASE_LOOP_LOOKBACK_SECONDS,
        "cutoff_at": cutoff,
        "pending_exit_no_command_count": no_command_count,
        "pending_exit_no_command_sample": no_command_sample,
        "pending_exit_no_command_skip_reason": no_command_skip_reason,
        "pending_exit_release_loop_count": loop_count,
        "pending_exit_release_loop_sample": sample,
        "pending_exit_reassert_loop_count": reassert_count,
        "pending_exit_reassert_loop_sample": reassert_sample,
        "pending_exit_reassert_loop_min_intents": PENDING_EXIT_REASSERT_LOOP_MIN_INTENTS,
        "pending_exit_churn_lookback_seconds": PENDING_EXIT_CHURN_LOOKBACK_SECONDS,
        "pending_exit_churn_cutoff_at": churn_cutoff,
        "pending_exit_churn_min_intents": PENDING_EXIT_CHURN_MIN_INTENTS,
        "pending_exit_churn_min_rejections_or_releases": (
            PENDING_EXIT_CHURN_MIN_REJECTIONS_OR_RELEASES
        ),
        "pending_exit_churn_count": active_churn_count,
        "pending_exit_churn_sample": active_churn_sample,
        "pending_exit_churn_total_count": len(churn_sample),
        "pending_exit_churn_historical_stabilized_count": len(historical_churn_sample),
        "pending_exit_churn_historical_stabilized_sample": historical_churn_sample,
    }
    if no_command_count > 0:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_NO_EXIT_COMMAND:n={no_command_count}",
            **detail,
        }
    if loop_count > 0:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_RELEASE_LOOP:n={loop_count}",
            **detail,
        }
    if reassert_count > 0:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_REASSERT_LOOP:n={reassert_count}",
            **detail,
        }
    if active_churn_count > 0:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_CHURN:n={active_churn_count}",
            **detail,
        }
    return {"ok": True, "issue": None, **detail}


def _pending_exit_churn_is_current(row: dict, now: datetime) -> bool:
    phase = str(row.get("phase") or "")
    order_status = str(row.get("order_status") or "")
    if phase == "pending_exit" or order_status in {
        "exit_intent",
        "retry_pending",
        "backoff_exhausted",
        "sell_pending",
        "sell_placed",
    }:
        return True

    latest_event_at: datetime | None = None
    for key in (
        "latest_exit_intent_at",
        "latest_exit_rejection_at",
        "latest_exit_release_at",
    ):
        parsed = _parse_iso_utc(row.get(key))
        if parsed is not None and (
            latest_event_at is None or parsed > latest_event_at
        ):
            latest_event_at = parsed
    if latest_event_at is None:
        return False
    current_cutoff = now.astimezone(timezone.utc) - timedelta(
        seconds=PENDING_EXIT_RELEASE_LOOP_LOOKBACK_SECONDS
    )
    return latest_event_at >= current_cutoff


def _monitor_probability_freshness_surface(
    state_dir: Path,
    now: datetime,
    *,
    main_daemon_surface: dict,
) -> dict:
    """Detect active exposure whose latest monitor probability is not fresh."""

    if not bool(main_daemon_surface.get("attested")):
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_MAIN_DAEMON_NOT_ATTESTED",
            "evaluated": False,
        }

    trade_db = state_dir / "zeus_trades.db"
    position_columns, position_column_err = _sqlite_ro_table_columns(
        trade_db,
        "position_current",
    )
    if position_column_err:
        return {
            "ok": False,
            "issue": f"MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:{position_column_err}",
            "evaluated": True,
        }
    if not position_columns:
        return {
            "ok": True,
            "issue": None,
            "evaluated": False,
            "skip_reason": "POSITION_CURRENT_TABLE_MISSING",
        }
    required_position_columns = {
        "position_id",
        "phase",
        "order_status",
        "shares",
        "chain_shares",
        "last_monitor_prob",
        "last_monitor_prob_is_fresh",
        "updated_at",
    }
    missing_position_columns = sorted(required_position_columns - position_columns)
    if missing_position_columns:
        return {
            "ok": True,
            "issue": None,
            "evaluated": False,
            "skip_reason": "MONITOR_PROBABILITY_FRESHNESS_COLUMN_MISSING:"
            + ",".join(missing_position_columns),
            "missing_columns": missing_position_columns,
        }

    event_columns, event_column_err = _sqlite_ro_table_columns(trade_db, "position_events")
    if event_column_err:
        return {
            "ok": False,
            "issue": f"MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:{event_column_err}",
            "evaluated": True,
        }
    has_monitor_events = {
        "position_id",
        "sequence_no",
        "event_type",
        "occurred_at",
        "payload_json",
    }.issubset(event_columns)
    cutoff = (
        now.astimezone(timezone.utc)
        - timedelta(seconds=MONITOR_PROBABILITY_STALE_LOOKBACK_SECONDS)
    ).isoformat()

    current_sample, current_err = _sqlite_ro_rows(
        trade_db,
        """
        SELECT position_id,
               phase,
               order_status,
               shares,
               chain_shares,
               last_monitor_prob,
               last_monitor_prob_is_fresh,
               updated_at,
               city,
               target_date,
               bin_label,
               direction
          FROM position_current
         WHERE phase IN ('active', 'day0_window', 'pending_exit')
           AND (
               COALESCE(CAST(chain_shares AS REAL), 0.0) > 0.0
               OR COALESCE(CAST(shares AS REAL), 0.0) > 0.0
           )
           AND COALESCE(CAST(last_monitor_prob_is_fresh AS INTEGER), 0) != 1
         ORDER BY updated_at DESC, position_id
         LIMIT ?
        """,
        (MONITOR_PROBABILITY_STALE_SAMPLE_LIMIT,),
    )
    if current_err:
        return {
            "ok": False,
            "issue": f"MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:{current_err}",
            "evaluated": True,
        }

    latest_stale_sample: list[dict] = []
    if has_monitor_events:
        latest_stale_sample, latest_err = _sqlite_ro_rows(
            trade_db,
            """
            WITH active_positions AS (
                SELECT position_id
                  FROM position_current
                 WHERE phase IN ('active', 'day0_window', 'pending_exit')
                   AND (
                       COALESCE(CAST(chain_shares AS REAL), 0.0) > 0.0
                       OR COALESCE(CAST(shares AS REAL), 0.0) > 0.0
                   )
            ),
            recent AS (
                SELECT e.position_id,
                       e.sequence_no,
                       e.occurred_at,
                       json_extract(e.payload_json, '$.last_monitor_prob')
                           AS last_monitor_prob,
                       json_extract(e.payload_json, '$.last_monitor_prob_is_fresh')
                           AS last_monitor_prob_is_fresh,
                       ROW_NUMBER() OVER (
                           PARTITION BY e.position_id
                           ORDER BY e.sequence_no DESC, datetime(e.occurred_at) DESC
                       ) AS rn,
                       SUM(
                           CASE
                               WHEN json_extract(e.payload_json, '$.last_monitor_prob_is_fresh')
                                    IN (0, 'false')
                               THEN 1 ELSE 0
                           END
                       ) OVER (PARTITION BY e.position_id) AS stale_count
                  FROM position_events e
                  JOIN active_positions ap
                    ON ap.position_id = e.position_id
                 WHERE e.event_type = 'MONITOR_REFRESHED'
                   AND datetime(e.occurred_at) >= datetime(?)
            )
            SELECT pc.position_id,
                   pc.phase,
                   pc.order_status,
                   pc.shares,
                   pc.chain_shares,
                   recent.occurred_at AS latest_monitor_at,
                   recent.last_monitor_prob,
                   recent.last_monitor_prob_is_fresh,
                   recent.stale_count,
                   pc.city,
                   pc.target_date,
                   pc.bin_label,
                   pc.direction
              FROM recent
              JOIN position_current pc
                ON pc.position_id = recent.position_id
             WHERE recent.rn = 1
               AND recent.last_monitor_prob_is_fresh IN (0, 'false')
             ORDER BY datetime(recent.occurred_at) DESC, pc.position_id
             LIMIT ?
            """,
            (cutoff, MONITOR_PROBABILITY_STALE_SAMPLE_LIMIT),
        )
        if latest_err:
            return {
                "ok": False,
                "issue": f"MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:{latest_err}",
                "evaluated": True,
            }

    current_count = len(current_sample)
    latest_stale_count = len(latest_stale_sample)
    detail = {
        "evaluated": True,
        "lookback_seconds": MONITOR_PROBABILITY_STALE_LOOKBACK_SECONDS,
        "cutoff_at": cutoff,
        "current_stale_projection_count": current_count,
        "current_stale_projection_sample": current_sample,
        "latest_stale_monitor_count": latest_stale_count,
        "latest_stale_monitor_sample": latest_stale_sample,
        "position_events_evaluated": has_monitor_events,
    }
    if current_count > 0:
        return {
            "ok": False,
            "issue": f"MONITOR_PROBABILITY_STALE_CURRENT:n={current_count}",
            **detail,
        }
    if latest_stale_count > 0:
        return {
            "ok": False,
            "issue": f"MONITOR_PROBABILITY_STALE_LATEST:n={latest_stale_count}",
            **detail,
        }
    return {"ok": True, "issue": None, **detail}


def _day0_decision_trace_surface(
    state_dir: Path,
    now: datetime,
    *,
    main_daemon_surface: dict,
) -> dict:
    """Prove processed Day0 events leave a money-path trace.

    A processed ``DAY0_EXTREME_UPDATED`` row is only useful operationally if the
    operator can tell which of the mutually exclusive outcomes happened:
    command emitted, no-submit receipt, terminal no-trade/regret, or compile
    failure. This is observability only; it does not rank or gate trades.
    """

    if not bool(main_daemon_surface.get("attested")):
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_MAIN_DAEMON_NOT_ATTESTED",
            "evaluated": False,
        }

    world_db = state_dir / "zeus-world.db"
    trade_db = state_dir / "zeus_trades.db"
    cutoff = (
        now.astimezone(timezone.utc)
        - timedelta(seconds=DAY0_DECISION_TRACE_LOOKBACK_SECONDS)
    ).isoformat()
    day0_events, event_err = _sqlite_ro_rows(
        world_db,
        """
        SELECT event_id, entity_key, created_at
          FROM opportunity_events
         WHERE event_type = 'DAY0_EXTREME_UPDATED'
           AND created_at >= ?
         ORDER BY rowid DESC
         LIMIT ?
        """,
        (cutoff, DAY0_DECISION_TRACE_SAMPLE_LIMIT),
    )
    if event_err:
        return {
            "ok": False,
            "issue": f"DAY0_EVENT_READ_UNAVAILABLE:{event_err}",
            "evaluated": True,
        }
    if not day0_events:
        return {
            "ok": True,
            "issue": None,
            "evaluated": True,
            "recent_event_count": 0,
            "processed_event_count": 0,
            "missing_trace_count": 0,
        }

    event_ids = tuple(
        str(row.get("event_id") or "").strip()
        for row in day0_events
        if str(row.get("event_id") or "").strip()
    )
    if not event_ids:
        return {
            "ok": False,
            "issue": "DAY0_EVENT_ID_MISSING",
            "evaluated": True,
            "recent_event_count": len(day0_events),
        }
    placeholders = ",".join("?" for _ in event_ids)
    processing_rows, processing_err = _sqlite_ro_rows(
        world_db,
        f"""
        SELECT event_id, processing_status, processed_at, last_error
          FROM opportunity_event_processing
         WHERE consumer_name = 'edli_reactor_v1'
           AND event_id IN ({placeholders})
        """,
        event_ids,
    )
    if processing_err:
        return {
            "ok": False,
            "issue": f"DAY0_PROCESSING_READ_UNAVAILABLE:{processing_err}",
            "evaluated": True,
        }
    processing_by_event = {
        str(row.get("event_id") or ""): row for row in processing_rows
    }
    processed_event_ids = tuple(
        event_id
        for event_id in event_ids
        if str(processing_by_event.get(event_id, {}).get("processing_status") or "")
        == "processed"
    )
    missing: list[dict[str, object]] = []
    traced = 0
    trace_counts = _day0_trace_counts_for_events(
        world_db=world_db,
        trade_db=trade_db,
        event_ids=processed_event_ids,
    )
    for event in day0_events:
        event_id = str(event.get("event_id") or "").strip()
        if event_id not in processed_event_ids:
            continue
        trace_count = trace_counts.get(event_id, 0)
        if trace_count > 0:
            traced += 1
        else:
            missing.append(
                {
                    "event_id": event_id,
                    "entity_key": event.get("entity_key"),
                    "created_at": event.get("created_at"),
                    "processed_at": processing_by_event.get(event_id, {}).get("processed_at"),
                }
            )

    detail = {
        "evaluated": True,
        "lookback_seconds": DAY0_DECISION_TRACE_LOOKBACK_SECONDS,
        "recent_event_count": len(day0_events),
        "processed_event_count": len(processed_event_ids),
        "traced_processed_event_count": traced,
        "missing_trace_count": len(missing),
        "missing_trace_sample": missing[:5],
    }
    if missing:
        return {
            "ok": False,
            "issue": f"DAY0_PROCESSED_WITHOUT_DECISION_TRACE:n={len(missing)}",
            **detail,
        }
    return {"ok": True, "issue": None, **detail}


def _day0_trace_counts_for_events(
    *,
    world_db: Path,
    trade_db: Path,
    event_ids: tuple[str, ...],
) -> dict[str, int]:
    counts = {event_id: 0 for event_id in event_ids}
    if not event_ids:
        return counts
    _add_day0_trace_counts_from_db(
        world_db,
        counts,
        checks=(
            ("SELECT 1 FROM decision_compile_failures WHERE event_id = ? LIMIT 1", lambda event_id: (event_id,)),
            ("SELECT 1 FROM no_trade_regret_events WHERE event_id = ? LIMIT 1", lambda event_id: (event_id,)),
            ("SELECT 1 FROM edli_no_submit_receipts WHERE event_id = ? LIMIT 1", lambda event_id: (event_id,)),
            (
                """
                SELECT 1
                  FROM decision_certificates INDEXED BY idx_decision_certificates_semantic
                 WHERE certificate_type = 'ActionableTradeCertificate'
                   AND semantic_key >= ?
                   AND semantic_key < ?
                 LIMIT 1
                """,
                _actionable_semantic_range,
            ),
        ),
    )
    _add_day0_trace_counts_from_db(
        trade_db,
        counts,
        checks=(
            ("SELECT 1 FROM venue_commands WHERE decision_id LIKE ? LIMIT 1", lambda event_id: (f"%{event_id}%",)),
            (
                """
                SELECT 1
                  FROM decision_certificates INDEXED BY idx_decision_certificates_semantic
                 WHERE certificate_type = 'ActionableTradeCertificate'
                   AND semantic_key >= ?
                   AND semantic_key < ?
                 LIMIT 1
                """,
                _actionable_semantic_range,
            ),
        ),
    )
    return counts


def _actionable_semantic_range(event_id: str) -> tuple[str, str]:
    return (f"actionable:{event_id}:", f"actionable:{event_id};")


def _add_day0_trace_counts_from_db(
    path: Path,
    counts: dict[str, int],
    *,
    checks: tuple[tuple[str, object], ...],
) -> None:
    if not path.exists():
        return
    try:
        from src.state.db import _connect_read_only

        conn = _connect_read_only(path)
    except Exception:  # noqa: BLE001
        return
    try:
        for event_id in counts:
            for sql, params_builder in checks:
                try:
                    params = params_builder(event_id)  # type: ignore[operator]
                    if conn.execute(sql, params).fetchone() is not None:
                        counts[event_id] += 1
                except Exception:  # noqa: BLE001
                    continue
    finally:
        conn.close()


def compute_composite_live_health(
    *,
    state_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Compute and persist composite live-health status.

    Consults fourteen surfaces:
      1. heartbeat — daemon-heartbeat.json (alive + fresh timestamp)
      2. venue_heartbeat — external CLOB heartbeat/order-safety keeper
      3. runtime_code — loaded_sha.json vs current git HEAD
      4. main_daemon — status/heartbeat PID still points at src.main
      5. process_code — src.main PID start time vs live-money source mtimes
      6. run_mode  — scheduler_jobs_health.json entry for "_run_mode" job
      7. forecast_pipeline — current replacement/BPF scheduler health
      8. forecast_event_bridge — live posteriors reaching FSR event emission
      9. entry_q_version — recent entry orders retain q-authority identity
      10. pending_exit_release_loop — no repeated exit retry/reassert churn
      11. monitor_probability_freshness — active monitor probabilities are fresh
      12. day0_decision_trace — processed Day0 events have decision evidence
      13. status_summary — status_summary.json top-level timestamp freshness
      14. execution_capability — entry/exit side-effect gate

    Writes state/live_health_composite.json atomically.

    Returns dict with keys:
      healthy: bool
      status:  "HEALTHY" | "DEGRADED"
      failing_surfaces: list[str]
      surfaces: dict  (per-surface detail)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    sd = _state_dir(state_dir)
    failing: list[str] = []
    surfaces: dict = {}

    # ------------------------------------------------------------------ #
    # Surface 1: heartbeat                                                 #
    # ------------------------------------------------------------------ #
    hb_path = sd / "daemon-heartbeat.json"
    hb_data = _read_json(hb_path)
    if hb_data is None:
        hb_issue = "MISSING"
        hb_ok = False
    else:
        ts_str = hb_data.get("timestamp") or hb_data.get("written_at")
        if not ts_str:
            hb_issue = "NO_TIMESTAMP"
            hb_ok = False
        else:
            age = _age_seconds(ts_str, now)
            if age is None:
                hb_issue = "UNPARSEABLE_TIMESTAMP"
                hb_ok = False
            elif age > STATUS_FRESH_BUDGET_SECONDS:
                hb_issue = f"STALE({age:.0f}s)"
                hb_ok = False
            else:
                hb_issue = None
                hb_ok = True

    surfaces["heartbeat"] = {"ok": hb_ok, "issue": hb_issue}
    if not hb_ok:
        failing.append("heartbeat")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "heartbeat",
            hb_issue,
        )

    runtime_code_surface = _runtime_code_surface(sd)
    surfaces["runtime_code"] = runtime_code_surface
    if not runtime_code_surface["ok"]:
        failing.append("runtime_code")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "runtime_code",
            runtime_code_surface["issue"],
        )

    venue_surface = _venue_heartbeat_surface(sd, now)
    surfaces["venue_heartbeat"] = venue_surface
    if not venue_surface["ok"]:
        failing.append("venue_heartbeat")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "venue_heartbeat",
            venue_surface["issue"],
        )

    ss_path = sd / "status_summary.json"
    ss_data = _read_json(ss_path)
    main_daemon_surface = _main_daemon_surface(ss_data, hb_data)
    surfaces["main_daemon"] = main_daemon_surface
    if not main_daemon_surface["ok"]:
        failing.append("main_daemon")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "main_daemon",
            main_daemon_surface["issue"],
        )

    process_code_surface = _process_code_surface(main_daemon_surface)
    surfaces["process_code"] = process_code_surface
    if not process_code_surface["ok"]:
        failing.append("process_code")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "process_code",
            process_code_surface["issue"],
        )

    # ------------------------------------------------------------------ #
    # Surface 3: run_mode (scheduler_jobs_health.json)                    #
    # ------------------------------------------------------------------ #
    sj_path = sd / "scheduler_jobs_health.json"
    sj_data = _read_json(sj_path)
    if sj_data is None:
        rm_issue = "SCHEDULER_HEALTH_MISSING"
        rm_ok = False
    else:
        # The decorator writes "run_mode"; _run_mode itself writes
        # mode-specific keys such as "run_mode:opening_hunt" after catching
        # exceptions. Mode-specific failures are the business-plane authority:
        # the generic wrapper can still be OK because _run_mode swallows errors.
        entries = _run_mode_health_entries(
            sj_data,
            live_execution_mode=_live_execution_mode(),
        )
        failed_entries = [
            (key, value)
            for key, value in entries
            if str(value.get("status", "")).upper() == "FAILED"
        ]
        if failed_entries:
            key, entry = sorted(failed_entries)[0]
            reason = entry.get("last_failure_reason") or "unknown"
            rm_issue = f"RUN_MODE_FAILED[{key}]: {reason}"
            rm_ok = False
        elif not entries:
            # Tolerate: scheduler_jobs_health may not have an entry yet on first
            # boot before the first cycle has run — treat as HEALTHY (no evidence
            # of failure yet).
            rm_issue = None
            rm_ok = True
        else:
            rm_issue = None
            rm_ok = True

    surfaces["run_mode"] = {"ok": rm_ok, "issue": rm_issue}
    if not rm_ok:
        failing.append("run_mode")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "run_mode",
            rm_issue,
        )

    # ------------------------------------------------------------------ #
    forecast_surface = _forecast_pipeline_surface(sj_data)
    surfaces["forecast_pipeline"] = forecast_surface
    if not forecast_surface["ok"]:
        failing.append("forecast_pipeline")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "forecast_pipeline",
            forecast_surface["issue"],
        )

    forecast_event_bridge_surface = _forecast_to_event_bridge_surface(
        sd,
        now,
        main_daemon_surface=main_daemon_surface,
    )
    surfaces["forecast_event_bridge"] = forecast_event_bridge_surface
    if not forecast_event_bridge_surface["ok"]:
        failing.append("forecast_event_bridge")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "forecast_event_bridge",
            forecast_event_bridge_surface["issue"],
        )

    entry_q_version_surface = _entry_q_version_surface(
        sd,
        now,
        main_daemon_surface=main_daemon_surface,
    )
    surfaces["entry_q_version"] = entry_q_version_surface
    if not entry_q_version_surface["ok"]:
        failing.append("entry_q_version")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "entry_q_version",
            entry_q_version_surface["issue"],
        )

    pending_exit_loop_surface = _pending_exit_release_loop_surface(
        sd,
        now,
        main_daemon_surface=main_daemon_surface,
    )
    surfaces["pending_exit_release_loop"] = pending_exit_loop_surface
    if not pending_exit_loop_surface["ok"]:
        failing.append("pending_exit_release_loop")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "pending_exit_release_loop",
            pending_exit_loop_surface["issue"],
        )

    monitor_probability_surface = _monitor_probability_freshness_surface(
        sd,
        now,
        main_daemon_surface=main_daemon_surface,
    )
    surfaces["monitor_probability_freshness"] = monitor_probability_surface
    if not monitor_probability_surface["ok"]:
        failing.append("monitor_probability_freshness")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "monitor_probability_freshness",
            monitor_probability_surface["issue"],
        )

    day0_trace_surface = _day0_decision_trace_surface(
        sd,
        now,
        main_daemon_surface=main_daemon_surface,
    )
    surfaces["day0_decision_trace"] = day0_trace_surface
    if not day0_trace_surface["ok"]:
        failing.append("day0_decision_trace")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "day0_decision_trace",
            day0_trace_surface["issue"],
        )

    # Surface 4: status_summary freshness                                 #
    # ------------------------------------------------------------------ #
    conflict_surface = None
    if ss_data is None:
        ss_issue = "STATUS_SUMMARY_MISSING"
        ss_ok = False
    else:
        ts_str = ss_data.get("timestamp")
        if not ts_str:
            ss_issue = "STATUS_SUMMARY_NO_TIMESTAMP"
            ss_ok = False
        else:
            age = _age_seconds(ts_str, now)
            if age is None:
                ss_issue = "STATUS_SUMMARY_UNPARSEABLE_TIMESTAMP"
                ss_ok = False
            elif age > STATUS_FRESH_BUDGET_SECONDS:
                ss_issue = f"STATUS_SUMMARY_STALE({age:.0f}s)"
                ss_ok = False
            else:
                conflict_surface = _status_summary_order_state_conflict_surface(
                    ss_data,
                    state_dir=sd,
                )
                ss_issue = conflict_surface["issue"]
                ss_ok = bool(conflict_surface["ok"])

    surfaces["status_summary"] = {"ok": ss_ok, "issue": ss_issue}
    if ss_data is not None and conflict_surface is not None:
        surfaces["status_summary"].update(conflict_surface)
    if not ss_ok:
        failing.append("status_summary")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "status_summary",
            ss_issue,
        )

    business_surface = _business_plane_surface(ss_data)
    surfaces["business_plane"] = business_surface
    if not business_surface["ok"]:
        failing.append("business_plane")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "business_plane",
            business_surface["issue"],
        )

    execution_surface = _execution_capability_surface(ss_data)
    surfaces["execution_capability"] = execution_surface
    if not execution_surface["ok"]:
        failing.append("execution_capability")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "execution_capability",
            execution_surface["issue"],
        )

    # ------------------------------------------------------------------ #
    # Assemble result                                                      #
    # ------------------------------------------------------------------ #
    healthy = len(failing) == 0
    result: dict = {
        "healthy": healthy,
        "status": "HEALTHY" if healthy else "DEGRADED",
        "failing_surfaces": failing,
        "surfaces": surfaces,
        "computed_at": now.isoformat(),
    }

    # ------------------------------------------------------------------ #
    # Persist atomically                                                   #
    # ------------------------------------------------------------------ #
    out_path = sd / "live_health_composite.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(out_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(result, f, indent=2, sort_keys=True)
            os.replace(tmp, str(out_path))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        logger.debug("live_health_composite: write failed", exc_info=True)

    return result
