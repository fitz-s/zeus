# Created: 2026-05-19
# Last reused or audited: 2026-07-15
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

import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

STATUS_FRESH_BUDGET_SECONDS = 300  # 5 minutes — consistent with heartbeat budget
FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS = STATUS_FRESH_BUDGET_SECONDS
DAY0_DECISION_TRACE_LOOKBACK_SECONDS = 3600
DAY0_DECISION_TRACE_SAMPLE_LIMIT = 50
FORECAST_DECISION_TRACE_LOOKBACK_SECONDS = 48 * 3600
FORECAST_DECISION_TRACE_SAMPLE_LIMIT = 25
HIGH_YES_EDGE_LOOKBACK_SECONDS = 48 * 3600
HIGH_YES_EDGE_MIN_Q_LCB = 0.50
VERY_HIGH_YES_EDGE_MIN_Q_LCB = 0.80
HIGH_YES_EDGE_MIN_LCB_MINUS_ASK = 0.05
HIGH_YES_EDGE_SAMPLE_LIMIT = 10
LIVE_BOOT_SIDECAR_HEARTBEATS = (
    ("forecast-live", "forecast-live-heartbeat.json", 120.0),
    ("substrate-observer", "daemon-heartbeat-substrate-observer.json", 180.0),
    ("price-channel-ingest", "daemon-heartbeat-price-channel-ingest.json", 180.0),
    ("post-trade-capital", "daemon-heartbeat-post-trade-capital.json", 180.0),
)
LIVE_BOOT_SIDECAR_CLOCK_SKEW_SECONDS = 5.0
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
PENDING_EXIT_PROJECTION_REGRESSION_LOOKBACK_SECONDS = 24 * 3600
PENDING_EXIT_RELEASE_LOOP_SAMPLE_LIMIT = 10
PENDING_EXIT_REASSERT_LOOP_MIN_INTENTS = 3
PENDING_EXIT_CHURN_MIN_INTENTS = 10
PENDING_EXIT_CHURN_MIN_REJECTIONS_OR_RELEASES = 3
MONITOR_PROBABILITY_STALE_LOOKBACK_SECONDS = 10 * 60
MONITOR_PROBABILITY_STALE_SAMPLE_LIMIT = 10
MONITOR_DAY0_SEMANTIC_SAMPLE_LIMIT = 10
SUB_MIN_PARTIAL_POSITION_SAMPLE_LIMIT = 10
PROCESS_CODE_STALE_TOLERANCE_SECONDS = 2
POSTERIOR_STALENESS_ALERT_HOURS_DEFAULT = 12.0
# Threshold rationale (2026-07-17, config/settings.json ops._posterior_staleness_alert_hours_rationale):
# 12h = one full provider refresh interval; the 30h forecast_posteriors TTL
# (expires_at) is the hard cliff after which entries silently starve. Alerting
# at 12h leaves an 18h repair window before that cliff. Incident proof: the
# 2026-07-13 08:15 CONUS live-posterior blackout onset would have alerted at
# 20:15 under this threshold, instead of running dark until the silent TTL
# expiry at 2026-07-14T06:00+.
POSTERIOR_STARVATION_REASON_MAX_CHARS = 300
_POSTERIOR_FAILED_RECEIPT_NAME = re.compile(
    r"^(?P<city>.+)\.(?P<target>\d{4}-\d{2}-\d{2})\."
    r"(?P<metric>high|low)\..+\.receipt\.json$"
)
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


def _short_sha_matches(expected_sha: str, heartbeat_sha: str) -> bool:
    expected = str(expected_sha or "").strip()
    heartbeat = str(heartbeat_sha or "").strip()
    if not expected or not heartbeat:
        return False
    return expected == heartbeat or (
        len(heartbeat) >= 7 and expected.startswith(heartbeat)
    )


def _live_boot_prerequisites_surface(state_dir: Path, now: datetime) -> dict:
    """Expose sidecar liveness prerequisites and code-identity observations."""

    current_sha = _current_git_head() or ""
    failures: list[str] = []
    identity_observations: list[str] = []
    if not current_sha:
        identity_observations.append("current_git_head_unavailable")
    ok_sidecars: list[dict[str, object]] = []
    for daemon, filename, max_age_seconds in LIVE_BOOT_SIDECAR_HEARTBEATS:
        path = state_dir / filename
        if not path.exists():
            failures.append(f"{daemon}:missing:{filename}")
            continue
        payload = _read_json(path)
        if payload is None:
            failures.append(f"{daemon}:unreadable:{filename}")
            continue
        heartbeat_sha = str(payload.get("git_head") or "").strip()
        if current_sha and not _short_sha_matches(current_sha, heartbeat_sha):
            identity_observations.append(
                f"{daemon}:git_head_mismatch heartbeat={heartbeat_sha or '<missing>'} "
                f"expected={current_sha[:8]}"
            )
        ts_str = (
            payload.get("alive_at")
            or payload.get("written_at")
            or payload.get("timestamp")
        )
        if not ts_str:
            failures.append(f"{daemon}:timestamp_missing")
            continue
        age = _age_seconds(str(ts_str), now)
        if age is None:
            failures.append(f"{daemon}:timestamp_invalid")
            continue
        if age < -LIVE_BOOT_SIDECAR_CLOCK_SKEW_SECONDS or age > max_age_seconds:
            failures.append(
                f"{daemon}:stale age_seconds={age:.1f} max={max_age_seconds:.1f}"
            )
            continue
        ok_sidecars.append(
            {
                "daemon": daemon,
                "git_head": heartbeat_sha,
                "age_seconds": age,
                "max_age_seconds": max_age_seconds,
            }
        )

    detail = {
        "evaluated": True,
        "expected_sha": current_sha,
        "ok_sidecars": ok_sidecars,
        "failures": failures,
        "identity_observations": identity_observations,
    }
    if failures:
        return {
            "ok": False,
            "issue": f"LIVE_BOOT_SIDECARS_NOT_READY:n={len(failures)}",
            **detail,
        }
    return {"ok": True, "issue": None, **detail}


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
    if candidates <= 0 and not zero_candidate_has_proof and not entry_unavailable_proof:
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
        capability = str(action.get("capability") or "").strip() or None
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
                        "capability": component.get("capability") or capability,
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
            "capability": capability,
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


def _live_trading_watchdog_surface(state_dir: Path, main_daemon_surface: dict) -> dict:
    """Detect launchd-watchdog status files that certify loaded-but-dead main."""

    path = state_dir / "live-trading-launchd-watchdog.json"
    payload = _read_json(path)
    if payload is None:
        return {
            "ok": True,
            "issue": None,
            "evaluated": False,
            "skip_reason": "LIVE_TRADING_WATCHDOG_STATUS_MISSING",
        }
    ok_claim = payload.get("ok") is True
    reason = str(payload.get("reason") or "")
    action = str(payload.get("action") or "")
    daemon_ok = bool(main_daemon_surface.get("ok")) and bool(
        main_daemon_surface.get("attested")
    )
    if ok_claim and reason == "service_loaded" and not daemon_ok:
        return {
            "ok": False,
            "issue": "LIVE_TRADING_WATCHDOG_FALSE_OK:service_loaded_not_running",
            "evaluated": True,
            "watchdog_ok": payload.get("ok"),
            "watchdog_action": action,
            "watchdog_reason": reason,
            "watchdog_written_at": payload.get("written_at"),
            "main_daemon_issue": main_daemon_surface.get("issue"),
            "main_daemon_attested": bool(main_daemon_surface.get("attested")),
        }
    return {
        "ok": True,
        "issue": None,
        "evaluated": True,
        "watchdog_ok": payload.get("ok"),
        "watchdog_action": action,
        "watchdog_reason": reason,
        "watchdog_written_at": payload.get("written_at"),
        "main_daemon_attested": bool(main_daemon_surface.get("attested")),
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
        latest_order = (
            "available_at DESC"
            if "available_at" in event_columns
            else "rowid DESC"
        )
        latest_fsr_rows, fsr_rows_err = _sqlite_ro_rows(
            world_db,
            f"""
            SELECT event_id, entity_key, created_at, {payload_select}
             FROM opportunity_events
             WHERE event_type = 'FORECAST_SNAPSHOT_READY'
             ORDER BY {latest_order}
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
    family_latest_posterior_at: datetime | None = None
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
            identity_scope_select = ""
            for scope_column in ("product_id", "source_id"):
                if scope_column in posterior_columns:
                    identity_scope_select += f", {scope_column}"
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
                       {identity_scope_select}
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
                    latest_comparison_at = posterior_at
                    family_columns = {
                        "city",
                        "target_date",
                        "temperature_metric",
                    }
                    if (
                        fsr_city
                        and fsr_target
                        and fsr_metric
                        and family_columns.issubset(posterior_columns)
                    ):
                        family_predicates = [
                            "runtime_layer = 'live'",
                            "city = ?",
                            "target_date = ?",
                            "temperature_metric = ?",
                        ]
                        family_params: list[str] = [
                            fsr_city,
                            fsr_target,
                            fsr_metric,
                        ]
                        for scope_column in ("product_id", "source_id"):
                            scope_value = str(
                                identity_match.get(scope_column) or ""
                            ).strip()
                            if scope_column in posterior_columns and scope_value:
                                family_predicates.append(f"{scope_column} = ?")
                                family_params.append(scope_value)
                        posterior_tiebreak = (
                            "posterior_id DESC"
                            if "posterior_id" in posterior_columns
                            else "rowid DESC"
                        )
                        family_rows, family_err = _sqlite_ro_rows(
                            forecast_db,
                            f"""
                            SELECT computed_at
                              FROM forecast_posteriors
                             WHERE {" AND ".join(family_predicates)}
                             ORDER BY COALESCE(source_cycle_time, '') DESC,
                                      COALESCE(computed_at, '') DESC,
                                      {posterior_tiebreak}
                             LIMIT 1
                            """,
                            tuple(family_params),
                        )
                        if family_err:
                            return {
                                "ok": False,
                                "issue": f"LIVE_POSTERIOR_READ_UNAVAILABLE:{family_err}",
                                "evaluated": True,
                            }
                        if family_rows:
                            parsed_family_latest = _parse_iso_utc(
                                family_rows[0].get("computed_at")
                            )
                            if parsed_family_latest is not None:
                                family_latest_posterior_at = parsed_family_latest
                                latest_comparison_at = parsed_family_latest
                    identity_to_latest_lag_seconds = (
                        latest_comparison_at - identity_computed_at
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
        "latest_fsr_family_latest_posterior_computed_at": (
            family_latest_posterior_at.isoformat()
            if family_latest_posterior_at is not None
            else None
        ),
        "event_queue": queue_detail,
        "max_lag_seconds": FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS,
    }
    queue_progress_at = _parse_iso_utc(
        queue_detail.get("latest_active_fsr_processing_updated_at")
    )
    queue_progress_age = (
        max(
            0.0,
            (now.astimezone(timezone.utc) - queue_progress_at).total_seconds(),
        )
        if queue_progress_at is not None
        else None
    )
    active_carrier_progress = bool(
        queue_detail.get("evaluated")
        and int(queue_detail.get("active_fsr_count") or 0) > 0
        and int(queue_detail.get("terminal_quality_retry_debt_count") or 0) == 0
        and queue_progress_age is not None
        and queue_progress_age <= FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS
    )
    detail["active_carrier_progress"] = active_carrier_progress
    detail["active_carrier_progress_age_seconds"] = queue_progress_age
    global_bridge_stalled = (
        lag_seconds > FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS
        and posterior_age > FORECAST_TO_EVENT_BRIDGE_BUDGET_SECONDS
    )
    if global_bridge_stalled and active_carrier_progress:
        return {
            "ok": True,
            "issue": None,
            "bridge_mode": "active_fsr_carrier_progress",
            **detail,
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
        if global_bridge_stalled:
            return {
                "ok": False,
                "issue": (
                    "FORECAST_TO_EVENT_BRIDGE_STALLED:"
                    f"posterior_newer_by={lag_seconds:.0f}s"
                ),
                **detail,
            }
        return {
            "ok": True,
            "issue": None,
            "bridge_mode": "fsr_identity_match",
            **detail,
        }
    if global_bridge_stalled:
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

    main_daemon_attested = bool(main_daemon_surface.get("attested"))
    main_daemon_issue = main_daemon_surface.get("issue")
    trade_db = state_dir / "zeus_trades.db"
    if not main_daemon_attested and not trade_db.exists():
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_MAIN_DAEMON_NOT_ATTESTED",
            "evaluated": False,
            "main_daemon_attested": False,
            "main_daemon_issue": main_daemon_issue,
        }

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

    projection_regression_cutoff = (
        now.astimezone(timezone.utc)
        - timedelta(seconds=PENDING_EXIT_PROJECTION_REGRESSION_LOOKBACK_SECONDS)
    ).isoformat()
    projection_regression_sample, projection_regression_err = _sqlite_ro_rows(
        trade_db,
        """
        WITH latest_exit AS (
            SELECT e.position_id,
                   e.sequence_no AS latest_exit_sequence,
                   e.event_type AS latest_exit_event_type,
                   e.occurred_at AS latest_exit_at,
                   e.phase_before AS latest_exit_phase_before,
                   e.phase_after AS latest_exit_phase_after,
                   e.venue_status AS latest_exit_venue_status,
                   json_extract(e.payload_json, '$.exit_reason') AS latest_exit_reason,
                   json_extract(e.payload_json, '$.error') AS latest_exit_error,
                   json_extract(e.payload_json, '$.status') AS latest_exit_status
              FROM position_events e
              JOIN (
                    SELECT position_id, MAX(sequence_no) AS latest_exit_sequence
                      FROM position_events
                     WHERE event_type IN (
                           'EXIT_INTENT',
                           'EXIT_ORDER_REJECTED',
                           'EXIT_ORDER_POSTED',
                           'EXIT_RETRY_RELEASED'
                       )
                       AND datetime(occurred_at) >= datetime(?)
                     GROUP BY position_id
                   ) le
                ON le.position_id = e.position_id
               AND le.latest_exit_sequence = e.sequence_no
        ),
        reasserted_positions AS (
            SELECT position_id
              FROM position_events
             WHERE datetime(occurred_at) >= datetime(?)
             GROUP BY position_id
            HAVING SUM(
                       CASE
                           WHEN event_type = 'EXIT_INTENT'
                            AND COALESCE(phase_before, '') = 'day0_window'
                            AND COALESCE(phase_after, '') = 'pending_exit'
                           THEN 1 ELSE 0
                       END
                   ) >= ?
        ),
        post_exit_held_projection AS (
            SELECT le.position_id,
                   COUNT(*) AS post_exit_held_event_count,
                   MIN(e.occurred_at) AS first_post_exit_held_at,
                   MAX(e.occurred_at) AS latest_post_exit_held_at,
                   MAX(e.sequence_no) AS latest_post_exit_held_sequence
              FROM latest_exit le
              JOIN position_events e
                ON e.position_id = le.position_id
               AND e.sequence_no > le.latest_exit_sequence
             WHERE e.event_type IN (
                   'MONITOR_REFRESHED',
                   'CHAIN_SIZE_CORRECTED',
                   'CHAIN_SYNCED'
               )
               AND COALESCE(e.phase_after, '') IN ('active', 'day0_window')
             GROUP BY le.position_id
        )
        SELECT pc.position_id,
               pc.phase,
               pc.order_status,
               pc.shares,
               pc.chain_shares,
               le.latest_exit_event_type,
               le.latest_exit_at,
               le.latest_exit_phase_before,
               le.latest_exit_phase_after,
               le.latest_exit_venue_status,
               le.latest_exit_reason,
               le.latest_exit_error,
               le.latest_exit_status,
               phe.post_exit_held_event_count,
               phe.first_post_exit_held_at,
               phe.latest_post_exit_held_at,
               phe.latest_post_exit_held_sequence,
               pc.city,
               pc.target_date,
               pc.bin_label,
               pc.direction,
               pc.exit_reason
          FROM latest_exit le
          JOIN post_exit_held_projection phe
            ON phe.position_id = le.position_id
          JOIN position_current pc
            ON pc.position_id = le.position_id
          LEFT JOIN reasserted_positions rp
            ON rp.position_id = le.position_id
         WHERE pc.phase IN ('active', 'day0_window')
           AND pc.order_status IN ('filled', 'partial')
           AND le.latest_exit_event_type <> 'EXIT_RETRY_RELEASED'
           AND rp.position_id IS NULL
           AND (
               COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
               OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
           )
         ORDER BY phe.latest_post_exit_held_at DESC, pc.position_id
         LIMIT ?
        """,
        (
            projection_regression_cutoff,
            cutoff,
            PENDING_EXIT_REASSERT_LOOP_MIN_INTENTS,
            PENDING_EXIT_RELEASE_LOOP_SAMPLE_LIMIT,
        ),
    )
    if projection_regression_err:
        return {
            "ok": False,
            "issue": (
                "PENDING_EXIT_RELEASE_LOOP_READ_UNAVAILABLE:"
                f"{projection_regression_err}"
            ),
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

    runtime_gate_sample, runtime_gate_err = _sqlite_ro_rows(
        trade_db,
        """
        WITH open_positions AS (
            SELECT position_id,
                   phase,
                   order_status,
                   shares,
                   chain_shares,
                   city,
                   target_date,
                   bin_label,
                   direction,
                   exit_reason
              FROM position_current
             WHERE phase IN ('active', 'day0_window', 'pending_exit')
               AND (
                   COALESCE(CAST(chain_shares AS REAL), 0.0) > 0.0
                   OR COALESCE(CAST(shares AS REAL), 0.0) > 0.0
               )
        ),
        gate_rejects AS (
            SELECT op.position_id,
                   COUNT(*) AS runtime_gate_reject_count,
                   MIN(e.occurred_at) AS first_runtime_gate_reject_at,
                   MAX(e.occurred_at) AS latest_runtime_gate_reject_at,
                   MAX(e.sequence_no) AS latest_runtime_gate_reject_sequence
              FROM open_positions op
              JOIN position_events e
                ON e.position_id = op.position_id
             WHERE e.event_type = 'EXIT_ORDER_REJECTED'
               AND (
                   json_extract(e.payload_json, '$.runtime_submit_gate_block') IN (1, 'true')
                   OR json_extract(e.payload_json, '$.status') = 'runtime_submit_gate_blocked'
                   OR (
                       json_extract(e.payload_json, '$.error') LIKE '%[gate_runtime] BLOCKED%'
                       AND (
                           json_extract(e.payload_json, '$.error') LIKE '%live_venue_submit%'
                           OR json_extract(e.payload_json, '$.error') LIKE '%reduce_only_exit_submit%'
                       )
                   )
               )
             GROUP BY op.position_id
        )
        SELECT op.position_id,
               op.phase,
               op.order_status,
               op.shares,
               op.chain_shares,
               gr.runtime_gate_reject_count,
               gr.first_runtime_gate_reject_at,
               gr.latest_runtime_gate_reject_at,
               json_extract(e.payload_json, '$.status') AS latest_runtime_gate_status,
               json_extract(e.payload_json, '$.exit_reason') AS latest_runtime_gate_exit_reason,
               json_extract(e.payload_json, '$.error') AS latest_runtime_gate_error,
               op.city,
               op.target_date,
               op.bin_label,
               op.direction,
               op.exit_reason
          FROM gate_rejects gr
          JOIN open_positions op
            ON op.position_id = gr.position_id
          JOIN position_events e
            ON e.position_id = gr.position_id
           AND e.sequence_no = gr.latest_runtime_gate_reject_sequence
         ORDER BY gr.runtime_gate_reject_count DESC,
                  gr.latest_runtime_gate_reject_at DESC,
                  op.position_id
         LIMIT ?
        """,
        (PENDING_EXIT_RELEASE_LOOP_SAMPLE_LIMIT,),
    )
    if runtime_gate_err:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_RELEASE_LOOP_READ_UNAVAILABLE:{runtime_gate_err}",
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
    projection_regression_count = len(projection_regression_sample)
    active_churn_count = len(active_churn_sample)
    no_command_count = len(no_command_sample)
    runtime_gate_count = len(runtime_gate_sample)
    detail = {
        "evaluated": True,
        "main_daemon_attested": main_daemon_attested,
        "main_daemon_issue": main_daemon_issue,
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
        "pending_exit_projection_regression_lookback_seconds": (
            PENDING_EXIT_PROJECTION_REGRESSION_LOOKBACK_SECONDS
        ),
        "pending_exit_projection_regression_cutoff_at": projection_regression_cutoff,
        "pending_exit_projection_regression_count": projection_regression_count,
        "pending_exit_projection_regression_sample": projection_regression_sample,
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
        "pending_exit_runtime_gate_block_count": runtime_gate_count,
        "pending_exit_runtime_gate_block_sample": runtime_gate_sample,
    }
    active_failure_parts = []
    if no_command_count > 0:
        active_failure_parts.append(f"no_exit_command={no_command_count}")
    if loop_count > 0:
        active_failure_parts.append(f"release_loop={loop_count}")
    if reassert_count > 0:
        active_failure_parts.append(f"reassert_loop={reassert_count}")
    if projection_regression_count > 0:
        active_failure_parts.append(
            f"projection_regression={projection_regression_count}"
        )
    if runtime_gate_count > 0:
        active_failure_parts.append(f"runtime_gate_block={runtime_gate_count}")
    if active_churn_count > 0:
        active_failure_parts.append(f"churn={active_churn_count}")
    if len(active_failure_parts) > 1:
        return {
            "ok": False,
            "issue": "PENDING_EXIT_MULTIPLE_FAILURES:" + ":".join(active_failure_parts),
            **detail,
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
    if projection_regression_count > 0:
        return {
            "ok": False,
            "issue": (
                f"PENDING_EXIT_PROJECTION_REGRESSION:n={projection_regression_count}"
            ),
            **detail,
        }
    if runtime_gate_count > 0:
        return {
            "ok": False,
            "issue": f"PENDING_EXIT_RUNTIME_GATE_BLOCK:n={runtime_gate_count}",
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

    main_daemon_attested = bool(main_daemon_surface.get("attested"))
    main_daemon_issue = main_daemon_surface.get("issue")
    trade_db = state_dir / "zeus_trades.db"
    if not main_daemon_attested and not trade_db.exists():
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_MAIN_DAEMON_NOT_ATTESTED",
            "evaluated": False,
            "main_daemon_attested": False,
            "main_daemon_issue": main_daemon_issue,
        }

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

    scoped_review_hold_sample: list[dict] = []
    closed_market_hold_sample: list[dict] = []
    closed_market_hold_revoked_exit_submit_sample: list[dict] = []
    if has_monitor_events:
        scoped_review_hold_sample, review_hold_err = _sqlite_ro_rows(
            trade_db,
            """
            SELECT pc.position_id,
                   pc.phase,
                   pc.order_status,
                   pc.shares,
                   pc.chain_shares,
                   latest.occurred_at AS latest_monitor_at,
                   pc.city,
                   pc.target_date,
                   pc.bin_label,
                   pc.direction
              FROM position_current pc
              JOIN position_events latest
                ON latest.rowid = (
                    SELECT e.rowid
                      FROM position_events e
                     WHERE e.position_id = pc.position_id
                       AND e.event_type = 'MONITOR_REFRESHED'
                     ORDER BY e.sequence_no DESC, datetime(e.occurred_at) DESC
                     LIMIT 1
                )
             WHERE pc.phase IN ('active', 'day0_window', 'pending_exit')
               AND (
                   COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                   OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
               )
               AND json_extract(latest.payload_json, '$.exit_decision_reason')
                   = 'REVIEW_REQUIRED_INVALID_ENTRY_PROOF'
               AND json_extract(
                       latest.payload_json,
                       '$.exit_decision_selected_method'
                   ) = 'chain_only_reconciliation'
               AND json_extract(
                       latest.payload_json,
                       '$.exit_decision_should_exit'
                   ) IN (0, 'false')
               AND EXISTS (
                   SELECT 1
                     FROM json_each(
                         json_extract(
                             latest.payload_json,
                             '$.applied_validations'
                         )
                     )
                    WHERE json_each.value = 'blocking_review_fact_monitor_hold'
               )
             ORDER BY datetime(latest.occurred_at) DESC, pc.position_id
            """,
        )
        if review_hold_err:
            return {
                "ok": False,
                "issue": f"MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:{review_hold_err}",
                "evaluated": True,
            }
        closed_market_hold_sample, closed_hold_err = _sqlite_ro_rows(
            trade_db,
            """
            SELECT pc.position_id,
                   pc.phase,
                   pc.order_status,
                   pc.shares,
                   pc.chain_shares,
                   closed.occurred_at AS market_closed_at,
                   pc.city,
                   pc.target_date,
                   pc.bin_label,
                   pc.direction
              FROM position_current pc
              JOIN position_events closed
                ON closed.rowid = (
                    SELECT e.rowid
                      FROM position_events e
                     WHERE e.position_id = pc.position_id
                       AND e.event_type = 'MONITOR_REFRESHED'
                       AND json_valid(e.payload_json)
                       AND json_extract(e.payload_json, '$.semantic_event')
                           = 'MARKET_CLOSED_HOLD_TO_SETTLEMENT'
                       AND json_extract(e.payload_json, '$.hold_reason')
                           = 'MARKET_CLOSED_AWAITING_SETTLEMENT'
                       AND json_extract(e.payload_json, '$.exit_order_submitted')
                           IN (0, 'false')
                       AND json_extract(e.payload_json, '$.exit_failure')
                           IN (0, 'false')
                       AND EXISTS (
                           SELECT 1
                             FROM json_each(
                                 json_extract(
                                     e.payload_json,
                                     '$.applied_validations'
                                 )
                             )
                            WHERE json_each.value
                                = 'MARKET_CLOSED_AWAITING_SETTLEMENT'
                       )
                       AND EXISTS (
                           SELECT 1
                             FROM json_each(
                                 json_extract(
                                     e.payload_json,
                                     '$.applied_validations'
                                 )
                             )
                            WHERE json_each.value
                                = 'closed_market_hold_preserved_monitor_evidence'
                       )
                     ORDER BY e.sequence_no DESC, datetime(e.occurred_at) DESC
                     LIMIT 1
                )
             WHERE pc.phase IN ('active', 'day0_window', 'pending_exit')
               AND (
                   COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                   OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM position_events later
                    WHERE later.position_id = pc.position_id
                      AND later.event_type = 'MONITOR_REFRESHED'
                      AND later.sequence_no > closed.sequence_no
                      AND json_valid(later.payload_json)
                      AND (
                          json_extract(
                              later.payload_json,
                              '$.exit_order_submitted'
                          ) IN (1, 'true')
                          OR (
                              json_extract(
                                  later.payload_json,
                                  '$.last_monitor_market_price_is_fresh'
                              ) IN (1, 'true')
                              AND json_extract(
                                  later.payload_json,
                                  '$.last_monitor_best_bid'
                              ) IS NOT NULL
                          )
                      )
               )
             ORDER BY datetime(closed.occurred_at) DESC, pc.position_id
            """,
        )
        if closed_hold_err:
            return {
                "ok": False,
                "issue": (
                    "MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:"
                    f"{closed_hold_err}"
                ),
                "evaluated": True,
            }
        command_columns, command_column_err = _sqlite_ro_table_columns(
            trade_db,
            "venue_commands",
        )
        event_command_columns, event_command_column_err = _sqlite_ro_table_columns(
            trade_db,
            "venue_command_events",
        )
        if command_column_err or event_command_column_err:
            return {
                "ok": False,
                "issue": (
                    "MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:"
                    f"{command_column_err or event_command_column_err}"
                ),
                "evaluated": True,
            }
        has_exit_submit_events = {
            "command_id",
            "position_id",
            "intent_kind",
            "state",
        }.issubset(command_columns) and {
            "command_id",
            "event_type",
            "occurred_at",
        }.issubset(event_command_columns)
        if closed_market_hold_sample and has_exit_submit_events:
            exit_submit_rows, exit_submit_err = _sqlite_ro_rows(
                trade_db,
                """
                SELECT vc.position_id,
                       vc.command_id,
                       vc.state AS command_state,
                       submitted.occurred_at AS submit_requested_at
                  FROM venue_commands vc
                  JOIN venue_command_events submitted
                    ON submitted.command_id = vc.command_id
                   AND submitted.event_type = 'SUBMIT_REQUESTED'
                 WHERE vc.intent_kind = 'EXIT'
                 ORDER BY datetime(submitted.occurred_at) DESC, vc.command_id
                """,
            )
            if exit_submit_err:
                return {
                    "ok": False,
                    "issue": (
                        "MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:"
                        f"{exit_submit_err}"
                    ),
                    "evaluated": True,
                }
            closed_at_by_position = {
                str(row["position_id"]): str(row.get("market_closed_at") or "")
                for row in closed_market_hold_sample
                if row.get("position_id") is not None
            }
            revoked_position_ids: set[str] = set()
            for row in exit_submit_rows:
                position_id = str(row.get("position_id") or "")
                closed_at_text = closed_at_by_position.get(position_id)
                if closed_at_text is None:
                    continue
                closed_at = _parse_iso_utc(closed_at_text)
                submitted_at = _parse_iso_utc(str(row.get("submit_requested_at") or ""))
                if (
                    closed_at is not None
                    and submitted_at is not None
                    and submitted_at < closed_at
                ):
                    continue
                if position_id in revoked_position_ids:
                    continue
                row["market_closed_at"] = closed_at_text
                row["revocation_reason"] = (
                    "exit_submit_requested_after_market_closed"
                    if closed_at is not None and submitted_at is not None
                    else "exit_submit_time_unparseable_fail_closed"
                )
                closed_market_hold_revoked_exit_submit_sample.append(row)
                revoked_position_ids.add(position_id)
            if revoked_position_ids:
                closed_market_hold_sample = [
                    row
                    for row in closed_market_hold_sample
                    if str(row.get("position_id")) not in revoked_position_ids
                ]
        elif closed_market_hold_sample:
            # Without canonical submit-request events, absence of a later EXIT
            # side effect is unprovable. Preserve the ordinary stale failure.
            closed_market_hold_sample = []
    review_hold_ids = {
        str(row["position_id"])
        for row in scoped_review_hold_sample
        if row.get("position_id") is not None
    }
    closed_market_hold_ids = {
        str(row["position_id"])
        for row in closed_market_hold_sample
        if row.get("position_id") is not None
    }
    current_latest_join = ""
    current_latest_payload = ""
    if has_monitor_events:
        current_latest_payload = (
            ", latest.payload_json AS latest_monitor_payload_json"
        )
        current_latest_join = """
          LEFT JOIN position_events latest
            ON latest.rowid = (
                SELECT e.rowid
                  FROM position_events e
                 WHERE e.position_id = pc.position_id
                   AND e.event_type = 'MONITOR_REFRESHED'
                 ORDER BY e.sequence_no DESC, datetime(e.occurred_at) DESC
                 LIMIT 1
            )
        """
    current_rows, current_err = _sqlite_ro_rows(
        trade_db,
        f"""
        SELECT pc.position_id,
               pc.phase,
               pc.order_status,
               pc.shares,
               pc.chain_shares,
               pc.last_monitor_prob,
               pc.last_monitor_prob_is_fresh,
               pc.updated_at,
               pc.city,
               pc.target_date,
               pc.bin_label,
               pc.direction
               {current_latest_payload}
          FROM position_current pc
          {current_latest_join}
         WHERE pc.phase IN ('active', 'day0_window', 'pending_exit')
           AND (
               COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
               OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
           )
           AND COALESCE(CAST(pc.last_monitor_prob_is_fresh AS INTEGER), 0) != 1
         ORDER BY pc.updated_at DESC, pc.position_id
        """,
        (),
    )
    if current_err:
        return {
            "ok": False,
            "issue": f"MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:{current_err}",
            "evaluated": True,
        }
    current_rows = [
        row
        for row in current_rows
        if str(row.get("position_id"))
        not in review_hold_ids | closed_market_hold_ids
    ]

    latest_stale_rows: list[dict] = []
    latest_monitor_age_rows: list[dict] = []
    day0_daily_extrema_unconditioned_sample: list[dict] = []
    if has_monitor_events:
        latest_stale_rows, latest_err = _sqlite_ro_rows(
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
                       e.payload_json,
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
                   recent.payload_json AS latest_monitor_payload_json,
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
            """,
            (cutoff,),
        )
        if latest_err:
            return {
                "ok": False,
                "issue": f"MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:{latest_err}",
                "evaluated": True,
            }
        latest_stale_rows = [
            row
            for row in latest_stale_rows
            if str(row.get("position_id"))
            not in review_hold_ids | closed_market_hold_ids
        ]

        latest_monitor_age_rows, latest_age_err = _sqlite_ro_rows(
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
            latest AS (
                SELECT e.position_id,
                       e.sequence_no,
                       e.occurred_at,
                       e.payload_json,
                       ROW_NUMBER() OVER (
                           PARTITION BY e.position_id
                           ORDER BY e.sequence_no DESC, datetime(e.occurred_at) DESC
                       ) AS rn
                  FROM position_events e
                  JOIN active_positions ap
                    ON ap.position_id = e.position_id
                 WHERE e.event_type = 'MONITOR_REFRESHED'
            )
            SELECT pc.position_id,
                   pc.phase,
                   pc.order_status,
                   pc.shares,
                   pc.chain_shares,
                   pc.last_monitor_prob,
                   pc.last_monitor_prob_is_fresh,
                   pc.updated_at,
                   latest.occurred_at AS latest_monitor_at,
                   latest.payload_json AS latest_monitor_payload_json,
                   pc.city,
                   pc.target_date,
                   pc.bin_label,
                   pc.direction
              FROM position_current pc
              LEFT JOIN latest
                ON latest.position_id = pc.position_id
               AND latest.rn = 1
             WHERE pc.phase IN ('active', 'day0_window', 'pending_exit')
               AND (
                   COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                   OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
               )
               AND COALESCE(CAST(pc.last_monitor_prob_is_fresh AS INTEGER), 0) = 1
               AND (
                   latest.occurred_at IS NULL
                   OR datetime(latest.occurred_at) < datetime(?)
               )
             ORDER BY datetime(latest.occurred_at) ASC, pc.position_id
            """,
            (cutoff,),
        )
        if latest_age_err:
            return {
                "ok": False,
                "issue": f"MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:{latest_age_err}",
                "evaluated": True,
            }
        for row in latest_monitor_age_rows:
            age_seconds = _age_seconds(str(row.get("latest_monitor_at") or ""), now)
            row["latest_monitor_age_seconds"] = age_seconds
            row["latest_monitor_stale_overage_seconds"] = (
                None
                if age_seconds is None
                else max(0.0, age_seconds - MONITOR_PROBABILITY_STALE_LOOKBACK_SECONDS)
            )
        latest_monitor_age_rows = [
            row
            for row in latest_monitor_age_rows
            if str(row.get("position_id"))
            not in review_hold_ids | closed_market_hold_ids
        ]
        for row in (*current_rows, *latest_stale_rows, *latest_monitor_age_rows):
            try:
                latest_payload = json.loads(
                    str(row.pop("latest_monitor_payload_json", "") or "{}")
                )
            except (TypeError, json.JSONDecodeError):
                latest_payload = {}
            if not isinstance(latest_payload, dict):
                latest_payload = {}
            validations = latest_payload.get("applied_validations")
            row["market_closed_hold_to_settlement"] = (
                latest_payload.get("semantic_event")
                == "MARKET_CLOSED_HOLD_TO_SETTLEMENT"
                and latest_payload.get("hold_reason")
                == "MARKET_CLOSED_AWAITING_SETTLEMENT"
                and latest_payload.get("exit_order_submitted") is False
                and latest_payload.get("exit_failure") is False
                and isinstance(validations, list)
                and "MARKET_CLOSED_AWAITING_SETTLEMENT" in validations
                and "closed_market_hold_preserved_monitor_evidence" in validations
            )

        day0_daily_extrema_unconditioned_sample, semantic_err = _sqlite_ro_rows(
            trade_db,
            """
            SELECT pc.position_id,
                   pc.phase,
                   pc.order_status,
                   pc.shares,
                   pc.chain_shares,
                   pc.last_monitor_prob,
                   pc.last_monitor_prob_is_fresh,
                   latest.occurred_at AS latest_monitor_at,
                   pc.city,
                   pc.target_date,
                   pc.bin_label,
                   pc.direction,
                   json_extract(
                       latest.payload_json,
                       '$.day0_monitor_probability_receipt.selected_method'
                   ) AS selected_method,
                   json_extract(
                       latest.payload_json,
                       '$.day0_monitor_probability_receipt.remaining_window.source'
                   ) AS remaining_window_source,
                   json_extract(
                       latest.payload_json,
                       '$.day0_monitor_probability_receipt.remaining_window.forecast_source_validations'
                   ) AS forecast_source_validations
              FROM position_current pc
              JOIN position_events latest
                ON latest.rowid = (
                    SELECT e.rowid
                      FROM position_events e
                     WHERE e.position_id = pc.position_id
                       AND e.event_type = 'MONITOR_REFRESHED'
                     ORDER BY e.sequence_no DESC, datetime(e.occurred_at) DESC
                     LIMIT 1
                )
             WHERE pc.phase IN ('active', 'day0_window', 'pending_exit')
               AND (
                   COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                   OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
               )
               AND EXISTS (
                   SELECT 1
                     FROM json_each(
                         json_extract(
                             latest.payload_json,
                             '$.day0_monitor_probability_receipt.remaining_window.forecast_source_validations'
                         )
                     )
                    WHERE json_each.value = 'forecast_source_role:day0_daily_extrema_live'
               )
               AND (
                   COALESCE(
                       json_extract(
                           latest.payload_json,
                           '$.day0_monitor_probability_receipt.selected_method'
                       ),
                       ''
                   ) != 'day0_observation_conditioned_daily_extrema'
                   OR COALESCE(
                       json_extract(
                           latest.payload_json,
                           '$.day0_monitor_probability_receipt.remaining_window.source'
                       ),
                       ''
                   ) != 'day0_observed_bound_conditioned_daily_extrema'
               )
             ORDER BY datetime(latest.occurred_at) DESC, pc.position_id
             LIMIT ?
            """,
            (MONITOR_DAY0_SEMANTIC_SAMPLE_LIMIT,),
        )
        if semantic_err:
            return {
                "ok": False,
                "issue": f"MONITOR_PROBABILITY_FRESHNESS_READ_UNAVAILABLE:{semantic_err}",
                "evaluated": True,
            }

    current_count = len(current_rows)
    latest_stale_count = len(latest_stale_rows)
    latest_age_count = len(latest_monitor_age_rows)
    semantic_count = len(day0_daily_extrema_unconditioned_sample)
    current_sample = current_rows[:MONITOR_PROBABILITY_STALE_SAMPLE_LIMIT]
    latest_stale_sample = latest_stale_rows[:MONITOR_PROBABILITY_STALE_SAMPLE_LIMIT]
    latest_monitor_age_sample = latest_monitor_age_rows[
        :MONITOR_PROBABILITY_STALE_SAMPLE_LIMIT
    ]
    detail = {
        "evaluated": True,
        "main_daemon_attested": main_daemon_attested,
        "main_daemon_issue": main_daemon_issue,
        "lookback_seconds": MONITOR_PROBABILITY_STALE_LOOKBACK_SECONDS,
        "cutoff_at": cutoff,
        "current_stale_projection_count": current_count,
        "current_stale_projection_truncated": current_count > len(current_sample),
        "current_stale_projection_sample": current_sample,
        "latest_stale_monitor_count": latest_stale_count,
        "latest_stale_monitor_truncated": latest_stale_count > len(latest_stale_sample),
        "latest_stale_monitor_sample": latest_stale_sample,
        "latest_monitor_age_stale_count": latest_age_count,
        "latest_monitor_age_stale_truncated": latest_age_count
        > len(latest_monitor_age_sample),
        "latest_monitor_age_stale_sample": latest_monitor_age_sample,
        "day0_daily_extrema_unconditioned_count": semantic_count,
        "day0_daily_extrema_unconditioned_sample": (
            day0_daily_extrema_unconditioned_sample
        ),
        "scoped_review_hold_count": len(scoped_review_hold_sample),
        "scoped_review_hold_sample": scoped_review_hold_sample,
        "closed_market_hold_to_settlement_count": len(closed_market_hold_sample),
        "closed_market_hold_to_settlement_sample": closed_market_hold_sample,
        "closed_market_hold_revoked_exit_submit_count": len(
            closed_market_hold_revoked_exit_submit_sample
        ),
        "closed_market_hold_revoked_exit_submit_sample": (
            closed_market_hold_revoked_exit_submit_sample
        ),
        "position_events_evaluated": has_monitor_events,
    }
    active_failure_parts = []
    if current_count > 0:
        active_failure_parts.append(f"current={current_count}")
    if latest_stale_count > 0:
        active_failure_parts.append(f"latest={latest_stale_count}")
    if latest_age_count > 0:
        active_failure_parts.append(f"age={latest_age_count}")
    if semantic_count > 0:
        active_failure_parts.append(f"day0_semantic={semantic_count}")
    if len(active_failure_parts) > 1:
        return {
            "ok": False,
            "issue": "MONITOR_PROBABILITY_MULTIPLE_FAILURES:"
            + ":".join(active_failure_parts),
            **detail,
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
    if latest_age_count > 0:
        return {
            "ok": False,
            "issue": f"MONITOR_PROBABILITY_STALE_AGE:n={latest_age_count}",
            **detail,
        }
    if semantic_count > 0:
        return {
            "ok": False,
            "issue": (
                "MONITOR_DAY0_DAILY_EXTREMA_USED_AS_REMAINING_WINDOW:"
                f"n={semantic_count}"
            ),
            **detail,
        }
    return {"ok": True, "issue": None, **detail}


def _sub_min_partial_position_surface(state_dir: Path, now: datetime) -> dict:
    """Detect open positions whose held shares are below the venue sell minimum."""

    trade_db = state_dir / "zeus_trades.db"
    if not trade_db.exists():
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_TRADE_DB_MISSING",
            "evaluated": False,
        }

    position_columns, position_column_err = _sqlite_ro_table_columns(
        trade_db,
        "position_current",
    )
    if position_column_err:
        return {
            "ok": False,
            "issue": f"SUB_MIN_PARTIAL_POSITION_READ_UNAVAILABLE:{position_column_err}",
            "evaluated": True,
        }
    if not position_columns:
        return {
            "ok": True,
            "issue": None,
            "evaluated": False,
            "skip_reason": "POSITION_CURRENT_TABLE_MISSING",
        }

    snapshot_columns, snapshot_column_err = _sqlite_ro_table_columns(
        trade_db,
        "executable_market_snapshots",
    )
    if snapshot_column_err:
        return {
            "ok": False,
            "issue": f"SUB_MIN_PARTIAL_POSITION_READ_UNAVAILABLE:{snapshot_column_err}",
            "evaluated": True,
        }
    if not snapshot_columns:
        return {
            "ok": True,
            "issue": None,
            "evaluated": False,
            "skip_reason": "EXECUTABLE_MARKET_SNAPSHOTS_TABLE_MISSING",
        }

    required_position_columns = {
        "position_id",
        "phase",
        "order_status",
        "shares",
        "chain_shares",
        "token_id",
        "condition_id",
    }
    required_snapshot_columns = {
        "condition_id",
        "selected_outcome_token_id",
        "snapshot_id",
        "min_order_size",
        "orderbook_top_bid",
        "orderbook_top_ask",
        "captured_at",
        "freshness_deadline",
    }
    missing_position_columns = sorted(required_position_columns - position_columns)
    missing_snapshot_columns = sorted(required_snapshot_columns - snapshot_columns)
    if missing_position_columns or missing_snapshot_columns:
        return {
            "ok": True,
            "issue": None,
            "evaluated": False,
            "skip_reason": "SUB_MIN_PARTIAL_POSITION_COLUMN_MISSING",
            "missing_position_columns": missing_position_columns,
            "missing_snapshot_columns": missing_snapshot_columns,
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
    rows, sample_err = _sqlite_ro_rows(
        trade_db,
        f"""
        WITH active_positions AS (
            SELECT pc.position_id,
                   pc.phase,
                   pc.order_status,
                   pc.shares,
                   pc.chain_shares,
                   pc.token_id,
                   pc.condition_id,
                   CASE
                       WHEN COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                       THEN COALESCE(CAST(pc.chain_shares AS REAL), 0.0)
                       ELSE COALESCE(CAST(pc.shares AS REAL), 0.0)
                   END AS held_shares,
                   {optional_position_select}
              FROM position_current pc
             WHERE pc.phase IN ('active', 'day0_window', 'pending_exit')
               AND COALESCE(pc.token_id, '') != ''
               AND COALESCE(pc.condition_id, '') != ''
               AND (
                   COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                   OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
               )
        )
        SELECT ap.position_id,
               ap.phase,
               ap.order_status,
               ap.shares,
               ap.chain_shares,
               ap.held_shares,
               ap.token_id,
               ap.condition_id,
               ap.city,
               ap.target_date,
               ap.bin_label,
               ap.direction,
               ap.exit_reason,
               ap.updated_at,
               s.snapshot_id,
               s.min_order_size,
               s.orderbook_top_bid,
               s.orderbook_top_ask,
               s.captured_at AS snapshot_captured_at,
               s.freshness_deadline AS snapshot_freshness_deadline
          FROM active_positions ap
          JOIN executable_market_snapshots s
            ON s.rowid = (
                SELECT s2.rowid
                  FROM executable_market_snapshots s2
                 WHERE s2.condition_id = ap.condition_id
                   AND s2.selected_outcome_token_id = ap.token_id
                   AND COALESCE(CAST(s2.min_order_size AS REAL), 0.0) > 0.0
                 ORDER BY datetime(s2.captured_at) DESC, s2.rowid DESC
                 LIMIT 1
            )
         WHERE ap.held_shares > 0.0
           AND ap.held_shares < COALESCE(CAST(s.min_order_size AS REAL), 0.0)
         ORDER BY ap.held_shares / COALESCE(CAST(s.min_order_size AS REAL), 1.0) ASC,
                  datetime(s.captured_at) DESC,
                  ap.position_id
        """,
        (),
    )
    if sample_err:
        return {
            "ok": False,
            "issue": f"SUB_MIN_PARTIAL_POSITION_READ_UNAVAILABLE:{sample_err}",
            "evaluated": True,
        }
    for row in rows:
        age_seconds = _age_seconds(str(row.get("snapshot_captured_at") or ""), now)
        row["snapshot_age_seconds"] = age_seconds
        row["snapshot_freshness_expired"] = (
            str(row.get("snapshot_freshness_deadline") or "")
            < now.astimezone(timezone.utc).isoformat()
        )

    count = len(rows)
    sample = rows[:SUB_MIN_PARTIAL_POSITION_SAMPLE_LIMIT]
    detail = {
        "evaluated": True,
        "sub_min_partial_position_count": count,
        "sub_min_partial_position_truncated": count > len(sample),
        "sub_min_partial_position_sample": sample,
    }
    if count > 0:
        return {
            "ok": False,
            "issue": f"SUB_MIN_PARTIAL_POSITION_UNEXITABLE:n={count}",
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
    requested = ",".join("(?)" for _ in event_ids)
    processing_rows, processing_err = _sqlite_ro_rows(
        world_db,
        f"""
        WITH requested(event_id) AS (VALUES {requested})
        SELECT p.event_id, p.processing_status, p.processed_at, p.last_error
          FROM requested r
          JOIN opportunity_event_processing p
            ON p.consumer_name = 'edli_reactor_v1'
           AND p.event_id = r.event_id
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


def _forecast_decision_trace_surface(
    state_dir: Path,
    now: datetime,
    *,
    main_daemon_surface: dict,
) -> dict:
    """Prove processed forecast events leave a money-path trace."""

    if not bool(main_daemon_surface.get("attested")):
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_MAIN_DAEMON_NOT_ATTESTED",
            "evaluated": False,
        }

    world_db = state_dir / "zeus-world.db"
    trade_db = state_dir / "zeus_trades.db"
    if not world_db.exists():
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_WORLD_DB_MISSING",
            "evaluated": False,
        }

    event_columns, event_err = _sqlite_ro_table_columns(world_db, "opportunity_events")
    if event_err:
        return {
            "ok": False,
            "issue": f"FORECAST_EVENT_READ_UNAVAILABLE:{event_err}",
            "evaluated": True,
        }
    processing_columns, processing_err = _sqlite_ro_table_columns(
        world_db,
        "opportunity_event_processing",
    )
    if processing_err:
        return {
            "ok": False,
            "issue": f"FORECAST_PROCESSING_READ_UNAVAILABLE:{processing_err}",
            "evaluated": True,
        }
    required_event_columns = {"event_id", "event_type", "entity_key", "created_at"}
    required_processing_columns = {
        "consumer_name",
        "event_id",
        "processing_status",
        "processed_at",
        "last_error",
    }
    missing_event = sorted(required_event_columns - event_columns)
    missing_processing = sorted(required_processing_columns - processing_columns)
    if missing_event or missing_processing:
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_FORECAST_TRACE_SCHEMA_MISSING",
            "evaluated": False,
            "missing_event_columns": missing_event,
            "missing_processing_columns": missing_processing,
        }

    cutoff = (
        now.astimezone(timezone.utc)
        - timedelta(seconds=FORECAST_DECISION_TRACE_LOOKBACK_SECONDS)
    ).isoformat()
    forecast_events, event_rows_err = _sqlite_ro_rows(
        world_db,
        """
        SELECT e.event_id,
               e.entity_key,
               e.created_at,
               p.processing_status,
               p.processed_at,
               p.last_error
          FROM opportunity_events e
          JOIN opportunity_event_processing p
            ON p.event_id = e.event_id
           AND p.consumer_name = 'edli_reactor_v1'
         WHERE e.event_type = 'FORECAST_SNAPSHOT_READY'
           AND e.created_at >= ?
           AND p.processing_status = 'processed'
         ORDER BY datetime(e.created_at) DESC, e.rowid DESC
         LIMIT ?
        """,
        (cutoff, FORECAST_DECISION_TRACE_SAMPLE_LIMIT),
    )
    if event_rows_err:
        return {
            "ok": False,
            "issue": f"FORECAST_TRACE_EVENT_READ_UNAVAILABLE:{event_rows_err}",
            "evaluated": True,
        }
    if not forecast_events:
        return {
            "ok": True,
            "issue": None,
            "evaluated": True,
            "lookback_seconds": FORECAST_DECISION_TRACE_LOOKBACK_SECONDS,
            "processed_event_count": 0,
            "missing_trace_count": 0,
        }

    event_ids = tuple(
        str(row.get("event_id") or "").strip()
        for row in forecast_events
        if str(row.get("event_id") or "").strip()
    )
    if not event_ids:
        return {
            "ok": False,
            "issue": "FORECAST_EVENT_ID_MISSING",
            "evaluated": True,
            "processed_event_count": len(forecast_events),
        }

    trace_counts = _decision_trace_counts_for_events(
        world_db=world_db,
        trade_db=trade_db,
        event_ids=event_ids,
    )
    missing: list[dict[str, object]] = []
    traced = 0
    for event in forecast_events:
        event_id = str(event.get("event_id") or "").strip()
        trace_count = trace_counts.get(event_id, 0)
        if trace_count > 0:
            traced += 1
            continue
        missing.append(
            {
                "event_id": event_id,
                "entity_key": event.get("entity_key"),
                "created_at": event.get("created_at"),
                "processed_at": event.get("processed_at"),
                "last_error": event.get("last_error"),
            }
        )

    detail = {
        "evaluated": True,
        "lookback_seconds": FORECAST_DECISION_TRACE_LOOKBACK_SECONDS,
        "processed_event_count": len(forecast_events),
        "traced_processed_event_count": traced,
        "missing_trace_count": len(missing),
        "missing_trace_sample": missing[:5],
    }
    if missing:
        return {
            "ok": False,
            "issue": f"FORECAST_PROCESSED_WITHOUT_DECISION_TRACE:n={len(missing)}",
            **detail,
        }
    return {"ok": True, "issue": None, **detail}


def _high_yes_edge_missed_surface(
    state_dir: Path,
    now: datetime,
    *,
    main_daemon_surface: dict,
) -> dict:
    """Detect high-confidence YES posterior/book edges with no YES action trace."""

    world_db = state_dir / "zeus-world.db"
    forecast_db = state_dir / "zeus-forecasts.db"
    trade_db = state_dir / "zeus_trades.db"
    missing_dbs = [
        name
        for name, path in (
            ("world", world_db),
            ("forecasts", forecast_db),
            ("trades", trade_db),
        )
        if not path.exists()
    ]
    if missing_dbs:
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_DB_MISSING:" + ",".join(missing_dbs),
            "evaluated": False,
        }

    schema_missing = _high_yes_edge_schema_missing(
        world_db=world_db,
        forecast_db=forecast_db,
        trade_db=trade_db,
    )
    if schema_missing:
        return {
            "ok": True,
            "issue": "NOT_EVALUATED_HIGH_YES_SCHEMA_MISSING",
            "evaluated": False,
            "missing_schema": schema_missing,
        }

    cutoff = (
        now.astimezone(timezone.utc)
        - timedelta(seconds=HIGH_YES_EDGE_LOOKBACK_SECONDS)
    ).isoformat()
    try:
        return _evaluate_high_yes_edge_missed_surface(
            world_db=world_db,
            forecast_db=forecast_db,
            trade_db=trade_db,
            cutoff=cutoff,
            now_iso=now.astimezone(timezone.utc).isoformat(),
            main_daemon_surface=main_daemon_surface,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "issue": f"HIGH_YES_EDGE_READ_UNAVAILABLE:{type(exc).__name__}:{exc}",
            "evaluated": True,
        }


def _evaluate_high_yes_edge_missed_surface(
    *,
    world_db: Path,
    forecast_db: Path,
    trade_db: Path,
    cutoff: str,
    now_iso: str,
    main_daemon_surface: dict,
) -> dict:
    return _evaluate_high_yes_edge_missed_surface_python(
        world_db=world_db,
        forecast_db=forecast_db,
        trade_db=trade_db,
        cutoff=cutoff,
        now_iso=now_iso,
        main_daemon_surface=main_daemon_surface,
    )


def _evaluate_high_yes_edge_missed_surface_python(
    *,
    world_db: Path,
    forecast_db: Path,
    trade_db: Path,
    cutoff: str,
    now_iso: str,
    main_daemon_surface: dict,
) -> dict:
    from src.state.db import _connect_read_only, query_control_override_state

    edges = _load_high_yes_edges_python(
        forecast_db=forecast_db,
        trade_db=trade_db,
        cutoff=cutoff,
        now_iso=now_iso,
    )
    condition_ids = sorted({str(row["condition_id"]) for row in edges})
    world_conn = _connect_read_only(world_db)
    try:
        buy_yes_suppression = _recent_buy_yes_suppression_summary(
            world_conn,
            cutoff=cutoff,
        )
        control_state = query_control_override_state(world_conn, now=now_iso)
        yes_no_submit_times = _directional_condition_times(
            world_conn,
            table="edli_no_submit_receipts",
            condition_ids=condition_ids,
            direction="buy_yes",
            time_columns=("created_at",),
            min_time=cutoff,
        )
        yes_no_trade_times = _directional_condition_times(
            world_conn,
            table="no_trade_regret_events",
            condition_ids=condition_ids,
            direction="buy_yes",
            time_columns=("created_at", "decision_time"),
            min_time=cutoff,
        )
        fsr_counts = _forecast_snapshot_status_counts_for_edges(
            world_conn,
            edges=edges,
            cutoff=cutoff,
        )
    finally:
        world_conn.close()

    trade_conn = _connect_read_only(trade_db)
    try:
        yes_candidates, no_candidates, auction_candidate_evidence = (
            _latest_global_auction_candidate_counts(trade_conn, cutoff=cutoff)
        )
        yes_entries = _yes_entry_command_counts(
            trade_conn,
            condition_ids=condition_ids,
            cutoff=cutoff,
        )
        recent_buy_yes_entry_command_count = _recent_buy_yes_entry_command_count(
            trade_conn,
            cutoff=cutoff,
        )
    finally:
        trade_conn.close()

    for edge in edges:
        condition_id = str(edge.get("condition_id") or "")
        edge_computed_at = str(edge.get("computed_at") or cutoff)
        auction_decision_at = str(
            auction_candidate_evidence.get("decision_at_utc") or ""
        )
        fsr_key = (
            str(edge.get("city") or ""),
            str(edge.get("target_date") or ""),
            str(edge.get("temperature_metric") or ""),
            edge_computed_at,
        )
        edge["yes_candidate_evidence_count"] = (
            yes_candidates.get(condition_id, 0)
            if auction_decision_at >= edge_computed_at
            else 0
        )
        edge["yes_entry_command_count"] = yes_entries.get(condition_id, 0)
        edge["yes_no_submit_count"] = _count_times_at_or_after(
            yes_no_submit_times.get(condition_id, ()),
            edge_computed_at,
        )
        edge["yes_no_trade_count"] = _count_times_at_or_after(
            yes_no_trade_times.get(condition_id, ()),
            edge_computed_at,
        )
        edge["no_candidate_evidence_count"] = (
            no_candidates.get(condition_id, 0)
            if auction_decision_at >= edge_computed_at
            else 0
        )
        status_counts = fsr_counts.get(fsr_key, {})
        edge["processed_fsr_count"] = status_counts.get("processed", 0)
        edge["pending_fsr_count"] = (
            status_counts.get("pending", 0) + status_counts.get("processing", 0)
        )
        edge["expired_fsr_count"] = status_counts.get("expired", 0)

    unresolved = [
        edge
        for edge in edges
        if edge["yes_candidate_evidence_count"] == 0
        and edge["yes_entry_command_count"] == 0
        and edge["yes_no_submit_count"] == 0
        and edge["yes_no_trade_count"] == 0
    ]
    pending_fsr = [
        edge for edge in unresolved if int(edge.get("pending_fsr_count") or 0) > 0
    ]
    missing_fsr = [
        edge
        for edge in unresolved
        if int(edge.get("processed_fsr_count") or 0) == 0
        and int(edge.get("pending_fsr_count") or 0) == 0
        and int(edge.get("expired_fsr_count") or 0) == 0
    ]
    processed_without_action = [
        edge for edge in unresolved if int(edge.get("processed_fsr_count") or 0) > 0
    ]
    missed = missing_fsr + processed_without_action
    very_high_count = sum(
        1 for edge in edges if float(edge.get("yes_lcb") or 0.0) >= VERY_HIGH_YES_EDGE_MIN_Q_LCB
    )
    missed_very_high_count = sum(
        1 for edge in missed if float(edge.get("yes_lcb") or 0.0) >= VERY_HIGH_YES_EDGE_MIN_Q_LCB
    )
    detail = {
        "evaluated": True,
        "main_daemon_attested": bool(main_daemon_surface.get("attested")),
        "main_daemon_issue": main_daemon_surface.get("issue"),
        "lookback_seconds": HIGH_YES_EDGE_LOOKBACK_SECONDS,
        "min_q_lcb": HIGH_YES_EDGE_MIN_Q_LCB,
        "very_high_min_q_lcb": VERY_HIGH_YES_EDGE_MIN_Q_LCB,
        "min_lcb_minus_ask": HIGH_YES_EDGE_MIN_LCB_MINUS_ASK,
        "high_yes_edge_count": len(edges),
        "very_high_yes_edge_count": very_high_count,
        "missed_high_yes_edge_count": len(missed),
        "missed_very_high_yes_edge_count": missed_very_high_count,
        "missed_high_yes_edge_sample": missed[:HIGH_YES_EDGE_SAMPLE_LIMIT],
        "pending_fsr_high_yes_edge_count": len(pending_fsr),
        "pending_fsr_high_yes_edge_sample": pending_fsr[:HIGH_YES_EDGE_SAMPLE_LIMIT],
        "missing_fsr_high_yes_edge_count": len(missing_fsr),
        "processed_without_action_high_yes_edge_count": len(processed_without_action),
        "global_auction_candidate_evidence": auction_candidate_evidence,
        "recent_buy_yes_entry_command_count": recent_buy_yes_entry_command_count,
        "entries_paused": bool(control_state.get("entries_paused")),
        "entries_pause_source": control_state.get("entries_pause_source"),
        "entries_pause_reason": control_state.get("entries_pause_reason"),
        "entries_pause_issued_at": control_state.get("entries_pause_issued_at"),
        "entries_pause_effective_until": control_state.get(
            "entries_pause_effective_until"
        ),
        **buy_yes_suppression,
    }
    candidate_evidence_issue = str(
        auction_candidate_evidence.get("issue") or ""
    )
    if candidate_evidence_issue:
        return {
            "ok": False,
            "issue": candidate_evidence_issue,
            **detail,
        }
    if detail["entries_paused"]:
        return {"ok": True, "issue": None, **detail}
    if missing_fsr:
        return {
            "ok": False,
            "issue": f"HIGH_YES_EDGE_WITHOUT_FSR:n={len(missing_fsr)}",
            **detail,
        }
    if processed_without_action:
        return {
            "ok": False,
            "issue": (
                "HIGH_YES_EDGE_PROCESSED_WITHOUT_ACTIONABLE_YES:"
                f"n={len(processed_without_action)}"
            ),
            **detail,
        }
    if pending_fsr:
        return {
            "ok": False,
            "issue": f"HIGH_YES_EDGE_FSR_PENDING:n={len(pending_fsr)}",
            **detail,
        }
    high_quality_no_trade_count = int(
        detail.get("recent_buy_yes_high_quality_no_trade_count") or 0
    )
    recent_buy_yes_no_submit_count = int(
        detail.get("recent_buy_yes_no_submit_count") or 0
    )
    if (
        high_quality_no_trade_count > 0
        and recent_buy_yes_entry_command_count == 0
        and recent_buy_yes_no_submit_count == 0
    ):
        return {
            "ok": False,
            "issue": (
                "HIGH_YES_QUALITY_SUPPRESSED_WITHOUT_ORDER_CHAIN:"
                f"n={high_quality_no_trade_count}"
            ),
            **detail,
        }
    return {"ok": True, "issue": None, **detail}


def _recent_buy_yes_entry_command_count(conn: object, *, cutoff: str) -> int | None:
    """Count recent buy-YES entry commands when the production schema exposes it."""

    try:
        columns = _connection_table_columns(conn, "venue_commands")
    except Exception:  # noqa: BLE001
        return None
    required = {"created_at", "intent_kind", "decision_id"}
    if not required.issubset(columns):
        return None
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM venue_commands
             WHERE intent_kind = 'ENTRY'
               AND created_at >= ?
               AND decision_id LIKE '%buy_yes%'
            """,
            (cutoff,),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return int(row["n"] if hasattr(row, "keys") else row[0])


def _load_high_yes_edges_python(
    *,
    forecast_db: Path,
    trade_db: Path,
    cutoff: str,
    now_iso: str,
) -> list[dict[str, object]]:
    from src.state.db import _connect_read_only

    forecast_conn = _connect_read_only(forecast_db)
    try:
        posterior_rows = forecast_conn.execute(
            """
            SELECT posterior_id,
                   city,
                   target_date,
                   temperature_metric,
                   computed_at,
                   q_json,
                   q_lcb_json
              FROM (
                    SELECT posterior_id,
                           city,
                           target_date,
                           temperature_metric,
                           computed_at,
                           q_json,
                           q_lcb_json,
                           ROW_NUMBER() OVER (
                               PARTITION BY city, target_date, temperature_metric
                               ORDER BY datetime(computed_at) DESC, posterior_id DESC
                           ) AS rn
                      FROM forecast_posteriors
                     WHERE computed_at >= ?
                       AND COALESCE(runtime_layer, 'live') = 'live'
                   )
             WHERE rn = 1
            """,
            (cutoff,),
        ).fetchall()
        families = sorted(
            {
                (
                    str(row["city"] or ""),
                    str(row["target_date"] or ""),
                    str(row["temperature_metric"] or ""),
                )
                for row in posterior_rows
                if row["city"] and row["target_date"] and row["temperature_metric"]
            }
        )
        market_rows = []
        for start in range(0, len(families), 250):
            chunk = families[start : start + 250]
            values_sql = ",".join("(?,?,?)" for _ in chunk)
            params = tuple(value for family in chunk for value in family)
            market_rows.extend(
                forecast_conn.execute(
                    f"""
                    WITH requested_families(city, target_date, metric) AS (
                        VALUES {values_sql}
                    )
                    SELECT DISTINCT m.city,
                           m.target_date,
                           m.temperature_metric,
                           m.range_label,
                           m.condition_id
                      FROM requested_families f
                      JOIN market_events m
                        ON m.city = f.city
                       AND m.target_date = f.target_date
                       AND m.temperature_metric = f.metric
                     WHERE m.condition_id IS NOT NULL
                       AND m.range_label IS NOT NULL
                    """,
                    params,
                ).fetchall()
            )
    finally:
        forecast_conn.close()

    market_by_family_bin: dict[tuple[str, str, str, str], list[str]] = {}
    for row in market_rows:
        key = (
            str(row["city"] or ""),
            str(row["target_date"] or ""),
            str(row["temperature_metric"] or ""),
            str(row["range_label"] or ""),
        )
        market_by_family_bin.setdefault(key, []).append(str(row["condition_id"]))

    trade_conn = _connect_read_only(trade_db)
    try:
        quote_rows = trade_conn.execute(
            """
            SELECT condition_id,
                   orderbook_top_ask,
                   captured_at,
                   freshness_deadline,
                   active,
                   closed,
                   accepting_orders
              FROM executable_market_snapshot_latest
             WHERE UPPER(COALESCE(outcome_label, '')) = 'YES'
               AND captured_at >= ?
               AND freshness_deadline >= ?
               AND COALESCE(active, 1) = 1
               AND COALESCE(closed, 0) = 0
               AND COALESCE(accepting_orders, 1) = 1
            """,
            (cutoff, now_iso),
        ).fetchall()
    finally:
        trade_conn.close()

    yes_quote_by_condition: dict[str, dict[str, object]] = {}
    for row in quote_rows:
        try:
            yes_ask = float(row["orderbook_top_ask"])
        except (TypeError, ValueError):
            continue
        if yes_ask <= 0.0 or yes_ask > 1.0:
            continue
        yes_quote_by_condition[str(row["condition_id"])] = {
            "yes_ask": yes_ask,
            "quote_captured_at": row["captured_at"],
            "quote_freshness_deadline": row["freshness_deadline"],
        }

    latest_by_condition: dict[str, dict[str, object]] = {}
    for row in posterior_rows:
        try:
            q_values = json.loads(row["q_json"] or "{}")
            q_lcb_values = json.loads(row["q_lcb_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(q_values, dict) or not isinstance(q_lcb_values, dict):
            continue
        city = str(row["city"] or "")
        target_date = str(row["target_date"] or "")
        metric = str(row["temperature_metric"] or "")
        for bin_label, raw_lcb in q_lcb_values.items():
            try:
                yes_lcb = float(raw_lcb)
            except (TypeError, ValueError):
                continue
            if yes_lcb < HIGH_YES_EDGE_MIN_Q_LCB:
                continue
            condition_ids = market_by_family_bin.get((city, target_date, metric, str(bin_label)), [])
            for condition_id in condition_ids:
                quote = yes_quote_by_condition.get(condition_id)
                if quote is None:
                    continue
                yes_ask = float(quote["yes_ask"])
                lcb_minus_ask = yes_lcb - yes_ask
                if lcb_minus_ask < HIGH_YES_EDGE_MIN_LCB_MINUS_ASK:
                    continue
                try:
                    yes_q = float(q_values.get(bin_label))
                except (TypeError, ValueError):
                    yes_q = None
                edge = {
                    "posterior_id": row["posterior_id"],
                    "city": city,
                    "target_date": target_date,
                    "temperature_metric": metric,
                    "bin_label": str(bin_label),
                    "condition_id": condition_id,
                    "computed_at": row["computed_at"],
                    "yes_q": yes_q,
                    "yes_lcb": yes_lcb,
                    "yes_ask": yes_ask,
                    "lcb_minus_ask": lcb_minus_ask,
                    **quote,
                }
                existing = latest_by_condition.get(condition_id)
                if existing is None or (
                    str(edge["computed_at"]),
                    int(edge["posterior_id"] or 0),
                ) > (
                    str(existing["computed_at"]),
                    int(existing["posterior_id"] or 0),
                ):
                    latest_by_condition[condition_id] = edge

    return sorted(
        latest_by_condition.values(),
        key=lambda edge: (
            float(edge.get("lcb_minus_ask") or 0.0),
            float(edge.get("yes_lcb") or 0.0),
            str(edge.get("computed_at") or ""),
        ),
        reverse=True,
    )


def _forecast_snapshot_status_counts_for_edges(
    conn: object,
    *,
    edges: list[dict[str, object]],
    cutoff: str,
) -> dict[tuple[str, str, str, str], dict[str, int]]:
    if not edges:
        return {}
    wanted = {
        (
            str(edge.get("city") or ""),
            str(edge.get("target_date") or ""),
            str(edge.get("temperature_metric") or ""),
            str(edge.get("computed_at") or ""),
        )
        for edge in edges
    }
    available_times = sorted({key[3] for key in wanted if key[3]})
    if not available_times:
        return {}
    placeholders = ",".join("?" for _ in available_times)
    try:
        rows = conn.execute(
            f"""
            SELECT e.event_id,
                   e.event_type,
                   e.available_at,
                   e.created_at,
                   e.payload_json,
                   p.processing_status
              FROM opportunity_events e
              LEFT JOIN opportunity_event_processing p
                ON p.event_id = e.event_id
               AND p.consumer_name = 'edli_reactor_v1'
             WHERE e.event_type IN ('FORECAST_SNAPSHOT_READY', 'EDLI_REDECISION_PENDING')
               AND e.available_at IN ({placeholders})
            """,
            tuple(available_times),
        ).fetchall()
    except Exception:  # noqa: BLE001 - optional trace detail must not mask the edge alarm
        return {}
    counts: dict[tuple[str, str, str, str], dict[str, int]] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        key = (
            str(payload.get("city") or ""),
            str(payload.get("target_date") or ""),
            str(payload.get("metric") or ""),
            str(payload.get("available_at") or row["available_at"] or ""),
        )
        if key not in wanted:
            continue
        status = str(row["processing_status"] or "pending").lower()
        if status == "processed":
            bucket = "processed"
        elif status in {"expired", "failed", "error"}:
            bucket = "expired"
        elif status == "processing":
            bucket = "processing"
        else:
            bucket = "pending"
        by_status = counts.setdefault(key, {})
        by_status[bucket] = by_status.get(bucket, 0) + 1
    return counts


def _recent_buy_yes_suppression_summary(conn: object, *, cutoff: str) -> dict[str, object]:
    """Summarize recent buy-YES audit artifacts for direction-balance diagnosis."""

    detail: dict[str, object] = {
        "recent_buy_yes_no_submit_count": None,
        "recent_buy_yes_no_trade_count": None,
        "recent_buy_yes_no_trade_top_reasons": [],
        "recent_buy_yes_no_trade_top_reason_classes": [],
        "recent_buy_yes_high_quality_no_trade_count": None,
        "recent_buy_yes_high_quality_no_trade_sample": [],
        "recent_buy_yes_degenerate_day0_lcb_no_trade_count": None,
        "recent_buy_yes_degenerate_day0_lcb_no_trade_sample": [],
        "recent_buy_yes_suppression_skip_reason": None,
    }
    try:
        table_rows = conn.execute(
            """
            SELECT name
              FROM sqlite_master
             WHERE type='table'
               AND name IN ('edli_no_submit_receipts', 'no_trade_regret_events')
            """
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        detail["recent_buy_yes_suppression_skip_reason"] = (
            f"schema_read_failed:{type(exc).__name__}"
        )
        return detail
    tables = {str(row["name"] if hasattr(row, "keys") else row[0]) for row in table_rows}

    if "edli_no_submit_receipts" in tables:
        try:
            columns = _connection_table_columns(conn, "edli_no_submit_receipts")
            decision_time_column = (
                "decision_time" if "decision_time" in columns else "created_at"
            )
            if {decision_time_column, "direction"}.issubset(columns):
                row = conn.execute(
                    f"""
                    SELECT COUNT(*) AS n
                      FROM edli_no_submit_receipts
                     WHERE direction = 'buy_yes'
                       AND {decision_time_column} >= ?
                    """,
                    (cutoff,),
                ).fetchone()
                detail["recent_buy_yes_no_submit_count"] = int(
                    row["n"] if hasattr(row, "keys") else row[0]
                )
        except Exception as exc:  # noqa: BLE001
            detail["recent_buy_yes_suppression_skip_reason"] = (
                f"no_submit_read_failed:{type(exc).__name__}"
            )

    if "no_trade_regret_events" in tables:
        try:
            columns = _connection_table_columns(conn, "no_trade_regret_events")
            if {"created_at", "direction"}.issubset(columns):
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                      FROM no_trade_regret_events
                     WHERE direction = 'buy_yes'
                       AND created_at >= ?
                    """,
                    (cutoff,),
                ).fetchone()
                detail["recent_buy_yes_no_trade_count"] = int(
                    row["n"] if hasattr(row, "keys") else row[0]
                )
            reason_columns = {
                "created_at",
                "direction",
                "rejection_stage",
                "rejection_reason",
            }
            if reason_columns.issubset(columns):
                reason_rows = conn.execute(
                    """
                    SELECT rejection_stage,
                           rejection_reason,
                           COUNT(*) AS n,
                           MIN(created_at) AS first_seen_at,
                           MAX(created_at) AS latest_seen_at
                      FROM no_trade_regret_events
                     WHERE direction = 'buy_yes'
                       AND created_at >= ?
                     GROUP BY rejection_stage, rejection_reason
                     ORDER BY n DESC, latest_seen_at DESC
                     LIMIT 5
                    """,
                    (cutoff,),
                ).fetchall()
                detail["recent_buy_yes_no_trade_top_reasons"] = [
                    {
                        "rejection_stage": row["rejection_stage"],
                        "rejection_reason": row["rejection_reason"],
                        "count": int(row["n"] or 0),
                        "first_seen_at": row["first_seen_at"],
                        "latest_seen_at": row["latest_seen_at"],
                    }
                    for row in reason_rows
                ]
                reason_class_rows = conn.execute(
                    """
                    SELECT rejection_stage,
                           CASE
                               WHEN instr(rejection_reason, ':candidate_id=') > 0
                               THEN substr(
                                   rejection_reason,
                                   1,
                                   instr(rejection_reason, ':candidate_id=') - 1
                               )
                               ELSE rejection_reason
                           END AS rejection_reason_class,
                           COUNT(*) AS n,
                           MIN(created_at) AS first_seen_at,
                           MAX(created_at) AS latest_seen_at
                      FROM no_trade_regret_events
                     WHERE direction = 'buy_yes'
                       AND created_at >= ?
                     GROUP BY rejection_stage, rejection_reason_class
                     ORDER BY n DESC, latest_seen_at DESC
                     LIMIT 5
                    """,
                    (cutoff,),
                ).fetchall()
                detail["recent_buy_yes_no_trade_top_reason_classes"] = [
                    {
                        "rejection_stage": row["rejection_stage"],
                        "rejection_reason_class": row["rejection_reason_class"],
                        "count": int(row["n"] or 0),
                        "first_seen_at": row["first_seen_at"],
                        "latest_seen_at": row["latest_seen_at"],
                    }
                    for row in reason_class_rows
                ]
            quality_columns = {
                "created_at",
                "direction",
                "q_lcb_5pct",
                "trade_score",
                "rejection_stage",
                "rejection_reason",
            }
            if quality_columns.issubset(columns):
                degenerate_row = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                      FROM no_trade_regret_events
                     WHERE direction = 'buy_yes'
                       AND created_at >= ?
                       AND rejection_reason LIKE ?
                    """,
                    (cutoff, "%remaining_day q_lcb is degenerate with q_live%"),
                ).fetchone()
                detail["recent_buy_yes_degenerate_day0_lcb_no_trade_count"] = int(
                    degenerate_row["n"] if hasattr(degenerate_row, "keys") else degenerate_row[0]
                )

                quality_row = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                      FROM no_trade_regret_events
                     WHERE direction = 'buy_yes'
                       AND created_at >= ?
                       AND COALESCE(CAST(q_lcb_5pct AS REAL), 0.0) >= ?
                       AND COALESCE(CAST(trade_score AS REAL), 0.0) > 0.0
                       AND rejection_reason NOT LIKE ?
                    """,
                    (
                        cutoff,
                        HIGH_YES_EDGE_MIN_Q_LCB,
                        "%remaining_day q_lcb is degenerate with q_live%",
                    ),
                ).fetchone()
                detail["recent_buy_yes_high_quality_no_trade_count"] = int(
                    quality_row["n"] if hasattr(quality_row, "keys") else quality_row[0]
                )
                def quality_column(column: str) -> str:
                    if column in columns:
                        return column
                    return f"NULL AS {column}"

                optional_quality_columns = [
                    quality_column("city"),
                    quality_column("target_date"),
                    quality_column("metric"),
                    quality_column("bin_label"),
                    quality_column("condition_id"),
                    quality_column("c_fee_adjusted"),
                ]
                quality_rows = conn.execute(
                    f"""
                    SELECT created_at,
                           rejection_stage,
                           rejection_reason,
                           q_lcb_5pct,
                           trade_score,
                           {", ".join(optional_quality_columns)}
                      FROM no_trade_regret_events
                     WHERE direction = 'buy_yes'
                       AND created_at >= ?
                       AND COALESCE(CAST(q_lcb_5pct AS REAL), 0.0) >= ?
                       AND COALESCE(CAST(trade_score AS REAL), 0.0) > 0.0
                       AND rejection_reason NOT LIKE ?
                     ORDER BY CAST(q_lcb_5pct AS REAL) DESC,
                              CAST(trade_score AS REAL) DESC,
                              created_at DESC
                     LIMIT ?
                    """,
                    (
                        cutoff,
                        HIGH_YES_EDGE_MIN_Q_LCB,
                        "%remaining_day q_lcb is degenerate with q_live%",
                        HIGH_YES_EDGE_SAMPLE_LIMIT,
                    ),
                ).fetchall()
                detail["recent_buy_yes_high_quality_no_trade_sample"] = [
                    {
                        "created_at": row["created_at"],
                        "rejection_stage": row["rejection_stage"],
                        "rejection_reason": row["rejection_reason"],
                        "q_lcb_5pct": row["q_lcb_5pct"],
                        "trade_score": row["trade_score"],
                        "city": row["city"],
                        "target_date": row["target_date"],
                        "metric": row["metric"],
                        "bin_label": row["bin_label"],
                        "condition_id": row["condition_id"],
                        "c_fee_adjusted": row["c_fee_adjusted"],
                    }
                    for row in quality_rows
                ]
                degenerate_rows = conn.execute(
                    f"""
                    SELECT created_at,
                           rejection_stage,
                           rejection_reason,
                           q_lcb_5pct,
                           trade_score,
                           {", ".join(optional_quality_columns)}
                      FROM no_trade_regret_events
                     WHERE direction = 'buy_yes'
                       AND created_at >= ?
                       AND rejection_reason LIKE ?
                     ORDER BY created_at DESC
                     LIMIT ?
                    """,
                    (
                        cutoff,
                        "%remaining_day q_lcb is degenerate with q_live%",
                        HIGH_YES_EDGE_SAMPLE_LIMIT,
                    ),
                ).fetchall()
                detail["recent_buy_yes_degenerate_day0_lcb_no_trade_sample"] = [
                    {
                        "created_at": row["created_at"],
                        "rejection_stage": row["rejection_stage"],
                        "rejection_reason": row["rejection_reason"],
                        "q_lcb_5pct": row["q_lcb_5pct"],
                        "trade_score": row["trade_score"],
                        "city": row["city"],
                        "target_date": row["target_date"],
                        "metric": row["metric"],
                        "bin_label": row["bin_label"],
                        "condition_id": row["condition_id"],
                        "c_fee_adjusted": row["c_fee_adjusted"],
                    }
                    for row in degenerate_rows
                ]
        except Exception as exc:  # noqa: BLE001
            detail["recent_buy_yes_suppression_skip_reason"] = (
                f"no_trade_read_failed:{type(exc).__name__}"
            )

    return detail


def _connection_table_columns(conn: object, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:  # noqa: BLE001
        return set()
    columns: set[str] = set()
    for row in rows:
        try:
            columns.add(str(row["name"]))
        except Exception:  # noqa: BLE001
            try:
                columns.add(str(row[1]))
            except Exception:  # noqa: BLE001
                continue
    return columns


def _yes_entry_command_counts(
    conn: object,
    *,
    condition_ids: list[str],
    cutoff: str,
) -> dict[str, int]:
    if not condition_ids:
        return {}
    rows = conn.execute(
        """
        SELECT ve.condition_id AS condition_id,
               vc.created_at AS created_at
          FROM venue_commands vc
          JOIN venue_submission_envelopes ve
            ON ve.envelope_id = vc.envelope_id
         WHERE vc.intent_kind = 'ENTRY'
           AND UPPER(COALESCE(vc.side, '')) = 'BUY'
           AND UPPER(COALESCE(ve.outcome_label, '')) = 'YES'
        """,
    ).fetchall()
    condition_set = set(condition_ids)
    counts: dict[str, int] = {}
    for row in rows:
        condition_id = str(row["condition_id"] or "")
        if condition_id in condition_set and str(row["created_at"] or "") >= cutoff:
            counts[condition_id] = counts.get(condition_id, 0) + 1
    return counts


def _directional_condition_times(
    conn: object,
    *,
    table: str,
    condition_ids: list[str],
    direction: str,
    time_columns: tuple[str, ...],
    min_time: str,
) -> dict[str, tuple[str, ...]]:
    if not condition_ids:
        return {}
    safe_table = table.replace('"', '""')
    try:
        columns = {
            str(row[1])
            for row in conn.execute(f'PRAGMA table_info("{safe_table}")').fetchall()
        }
    except Exception:  # noqa: BLE001
        return {}
    if not {"condition_id", "direction"}.issubset(columns):
        return {}
    time_col = next((column for column in time_columns if column in columns), None)
    if time_col is None:
        return {}
    placeholders = ",".join("?" for _ in condition_ids)
    try:
        rows = conn.execute(
            f"""
            SELECT condition_id,
                   {time_col} AS observed_at,
                   direction
              FROM "{safe_table}"
             WHERE condition_id IN ({placeholders})
               AND lower(COALESCE(direction, '')) = ?
               AND {time_col} >= ?
            """,
            (*condition_ids, direction, min_time),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    condition_set = set(condition_ids)
    times: dict[str, list[str]] = {}
    for row in rows:
        condition_id = str(row["condition_id"] or "")
        if condition_id not in condition_set:
            continue
        if str(row["direction"] or "").lower() != direction:
            continue
        observed_at = str(row["observed_at"] or "")
        if observed_at:
            times.setdefault(condition_id, []).append(observed_at)
    return {condition_id: tuple(sorted(values)) for condition_id, values in times.items()}


def _count_times_at_or_after(times: tuple[str, ...], cutoff: str) -> int:
    return sum(1 for observed_at in times if str(observed_at or "") >= cutoff)


_GLOBAL_AUCTION_RECEIPT_MODES = (
    "global_single_order_auction",
    "global_single_order_auction_delta",
    "global_single_order_auction_duplicate",
)


def _decode_global_auction_candidate_payload(
    summary: Mapping[str, object],
) -> tuple[dict[str, object], bytes]:
    compressed = base64.b64decode(
        str(summary["candidate_evaluations_zlib_b64"]),
        validate=True,
    )
    if len(compressed) > 2_000_000:
        raise ValueError("COMPRESSED_PAYLOAD_TOO_LARGE")
    raw = zlib.decompress(compressed)
    if len(raw) > 10_000_000:
        raise ValueError("PAYLOAD_TOO_LARGE")
    if hashlib.sha256(raw).hexdigest() != str(
        summary["candidate_evaluations_sha256"]
    ):
        raise ValueError("HASH_MISMATCH")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("PAYLOAD_SHAPE")
    return payload, raw


def _global_auction_reference_summary(
    conn: object,
    *,
    row_id: int,
    mode: str,
    receipt_hash: str,
    component_sha256: str,
) -> Mapping[str, object]:
    if mode not in _GLOBAL_AUCTION_RECEIPT_MODES:
        raise ValueError("REFERENCE_MODE")
    row = conn.execute(
        "SELECT mode, artifact_json FROM decision_log WHERE id = ?",
        (row_id,),
    ).fetchone()
    if row is None or str(row["mode"] or "") != mode:
        raise ValueError("REFERENCE_ROW")
    artifact = json.loads(str(row["artifact_json"] or ""))
    summary = artifact["summary"]
    if (
        str(summary.get("receipt_hash") or "") != receipt_hash
        or str(summary.get("candidate_evaluations_sha256") or "")
        != component_sha256
        or "candidate_evaluations_zlib_b64" not in summary
    ):
        raise ValueError("REFERENCE_IDENTITY")
    return summary


def _current_global_auction_candidate_payload(
    conn: object,
    summary: Mapping[str, object],
) -> dict[str, object]:
    field = "candidate_evaluations_zlib_b64"
    if field in summary:
        return _decode_global_auction_candidate_payload(summary)[0]

    delta_field = "candidate_evaluations_delta_zlib_b64"
    if delta_field in summary:
        if summary.get("candidate_evaluations_delta_encoding") != (
            "zlib+base64+canonical-json-object-delta-v1"
        ):
            raise ValueError("DELTA_ENCODING")
        base_row_id = int(summary["candidate_evaluations_base_decision_log_id"])
        base_receipt_hash = str(
            summary["candidate_evaluations_base_receipt_hash"]
        )
        base_mode = str(summary.get("candidate_evaluations_base_mode") or "")
        if not base_mode:
            if (
                int(summary.get("payload_reference_decision_log_id") or 0)
                != base_row_id
                or str(summary.get("payload_reference_receipt_hash") or "")
                != base_receipt_hash
            ):
                raise ValueError("DELTA_BASE_MODE_MISSING")
            base_mode = str(summary.get("payload_reference_mode") or "")
        base_summary = _global_auction_reference_summary(
            conn,
            row_id=base_row_id,
            mode=base_mode,
            receipt_hash=base_receipt_hash,
            component_sha256=str(summary["candidate_evaluations_base_sha256"]),
        )
        if base_summary.get("candidate_evaluation_encoding") != summary.get(
            "candidate_evaluation_encoding"
        ):
            raise ValueError("DELTA_BASE_ENCODING")
        base = _decode_global_auction_candidate_payload(base_summary)[0]
        compressed = base64.b64decode(str(summary[delta_field]), validate=True)
        if len(compressed) > 2_000_000:
            raise ValueError("DELTA_COMPRESSED_PAYLOAD_TOO_LARGE")
        delta_raw = zlib.decompress(compressed)
        if len(delta_raw) > 10_000_000:
            raise ValueError("DELTA_PAYLOAD_TOO_LARGE")
        if hashlib.sha256(delta_raw).hexdigest() != str(
            summary["candidate_evaluations_delta_sha256"]
        ):
            raise ValueError("DELTA_HASH_MISMATCH")
        delta = json.loads(delta_raw)
        replacements = delta.get("replacements", {})
        if not isinstance(replacements, dict):
            raise ValueError("DELTA_PAYLOAD_SHAPE")
        payload = dict(base)
        for key in delta.get("removed_keys", ()):
            payload.pop(str(key), None)
        payload.update((str(key), value) for key, value in replacements.items())
        raw = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != str(
            summary["candidate_evaluations_sha256"]
        ):
            raise ValueError("DELTA_RECONSTRUCTION_HASH_MISMATCH")
        return payload

    if field not in set(summary.get("payload_reference_fields", ())):
        raise ValueError("PAYLOAD_REFERENCE_MISSING")
    references = summary.get("payload_reference_components", {})
    component = references.get(field, {}) if isinstance(references, dict) else {}
    if component:
        row_id = int(component["decision_log_id"])
        mode = str(component["mode"])
        receipt_hash = str(component["receipt_hash"])
        component_sha256 = str(component["sha256"])
    else:
        row_id = int(summary["payload_reference_decision_log_id"])
        mode = str(summary["payload_reference_mode"])
        receipt_hash = str(summary["payload_reference_receipt_hash"])
        component_sha256 = str(summary["candidate_evaluations_sha256"])
    if component_sha256 != str(summary["candidate_evaluations_sha256"]):
        raise ValueError("PAYLOAD_REFERENCE_HASH_MISMATCH")
    reference_summary = _global_auction_reference_summary(
        conn,
        row_id=row_id,
        mode=mode,
        receipt_hash=receipt_hash,
        component_sha256=component_sha256,
    )
    if reference_summary.get("candidate_evaluation_encoding") != summary.get(
        "candidate_evaluation_encoding"
    ):
        raise ValueError("PAYLOAD_REFERENCE_ENCODING")
    return _decode_global_auction_candidate_payload(reference_summary)[0]


def _latest_global_auction_candidate_counts(
    conn: object,
    *,
    cutoff: str,
) -> tuple[dict[str, int], dict[str, int], dict[str, object]]:
    """Read condition-level BUY evidence from the latest complete auction receipt."""

    columns = _connection_table_columns(conn, "decision_log")
    required = {"id", "mode", "artifact_json", "timestamp"}
    if not required.issubset(columns):
        return {}, {}, {
            "evaluated": False,
            "issue": None,
            "skip_reason": "GLOBAL_AUCTION_DECISION_LOG_UNAVAILABLE",
        }
    row = conn.execute(
        """
        SELECT id, mode, artifact_json, timestamp
          FROM decision_log
         WHERE mode IN (?, ?, ?)
           AND timestamp >= ?
         ORDER BY id DESC
         LIMIT 1
        """,
        (*_GLOBAL_AUCTION_RECEIPT_MODES, cutoff),
    ).fetchone()
    if row is None:
        return {}, {}, {
            "evaluated": True,
            "issue": None,
            "skip_reason": "GLOBAL_AUCTION_RECEIPT_MISSING",
        }

    receipt_id = row["id"]

    def invalid(reason: str) -> tuple[dict[str, int], dict[str, int], dict[str, object]]:
        return {}, {}, {
            "evaluated": True,
            "issue": f"GLOBAL_AUCTION_CANDIDATE_EVIDENCE_INVALID:{reason}",
            "receipt_id": receipt_id,
        }

    try:
        artifact = json.loads(str(row["artifact_json"] or ""))
        summary = artifact["summary"]
        if int(summary.get("schema_version") or 0) < 5:
            return invalid("SCHEMA_VERSION")
        if summary.get("candidate_coverage_complete") is not True:
            return invalid("COVERAGE_INCOMPLETE")
        if summary.get("candidate_condition_index_complete") is not True:
            return invalid("CONDITION_INDEX_INCOMPLETE")
        if summary.get("candidate_evaluation_encoding") not in {
            "zlib+base64+canonical-json-v4",
            "zlib+base64+canonical-json-v5",
            "zlib+base64+canonical-json-v6",
            "zlib+base64+canonical-json-v7",
            "zlib+base64+canonical-json-v8",
            "zlib+base64+canonical-json-v9",
            "zlib+base64+canonical-json-v10",
        }:
            return invalid("ENCODING")
        payload = _current_global_auction_candidate_payload(conn, summary)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, zlib.error) as exc:
        return invalid(type(exc).__name__)

    yes: dict[str, int] = {}
    no: dict[str, int] = {}
    covered = 0
    try:
        for group in payload["rejected_groups"]:
            candidate_ids = group["candidate_ids"]
            covered += len(candidate_ids)
        for evaluation in payload["detailed"]:
            covered += 1
        seen_conditions: set[str] = set()
        for condition_id, raw_mask in payload["buy_condition_side_masks"]:
            condition_id = str(condition_id or "")
            mask = int(raw_mask)
            if not condition_id or condition_id in seen_conditions or mask not in {1, 2, 3}:
                return invalid("CONDITION_SIDE_MASK_INVALID")
            seen_conditions.add(condition_id)
            if mask & 1:
                yes[condition_id] = 1
            if mask & 2:
                no[condition_id] = 1
        expected = int(summary["candidate_evaluation_count"])
        expected_memberships = int(summary["buy_condition_membership_count"])
    except (KeyError, TypeError, ValueError):
        return invalid("PAYLOAD_SHAPE")
    if covered != expected:
        return invalid("EVALUATION_COUNT_MISMATCH")
    if len(yes) + len(no) != expected_memberships:
        return invalid("CONDITION_MEMBERSHIP_COUNT_MISMATCH")
    return yes, no, {
        "evaluated": True,
        "issue": None,
        "receipt_id": receipt_id,
        "decision_at_utc": str(
            summary.get("decision_at_utc") or row["timestamp"] or ""
        ),
        "candidate_evaluation_count": expected,
        "yes_condition_count": len(yes),
        "no_condition_count": len(no),
    }




def _high_yes_edge_schema_missing(
    *,
    world_db: Path,
    forecast_db: Path,
    trade_db: Path,
) -> dict[str, list[str]]:
    required = {
        "world.edli_no_submit_receipts": (
            world_db,
            "edli_no_submit_receipts",
            {"created_at", "condition_id", "direction"},
        ),
        "forecast.forecast_posteriors": (
            forecast_db,
            "forecast_posteriors",
            {
                "posterior_id",
                "city",
                "target_date",
                "temperature_metric",
                "computed_at",
                "runtime_layer",
                "q_json",
                "q_lcb_json",
            },
        ),
        "forecast.market_events": (
            forecast_db,
            "market_events",
            {"city", "target_date", "temperature_metric", "range_label", "condition_id"},
        ),
        "trade.executable_market_snapshot_latest": (
            trade_db,
            "executable_market_snapshot_latest",
            {
                "condition_id",
                "outcome_label",
                "orderbook_top_ask",
                "captured_at",
                "freshness_deadline",
                "active",
                "closed",
                "accepting_orders",
            },
        ),
        "trade.venue_commands": (
            trade_db,
            "venue_commands",
            {"intent_kind", "created_at", "side", "market_id", "envelope_id"},
        ),
        "trade.venue_submission_envelopes": (
            trade_db,
            "venue_submission_envelopes",
            {"envelope_id", "condition_id", "outcome_label"},
        ),
    }
    missing: dict[str, list[str]] = {}
    for label, (path, table, columns) in required.items():
        actual, err = _sqlite_ro_table_columns(path, table)
        if err:
            missing[label] = [err]
            continue
        absent = sorted(columns - actual)
        if absent:
            missing[label] = absent
    return missing


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _day0_trace_counts_for_events(
    *,
    world_db: Path,
    trade_db: Path,
    event_ids: tuple[str, ...],
) -> dict[str, int]:
    return _decision_trace_counts_for_events(
        world_db=world_db,
        trade_db=trade_db,
        event_ids=event_ids,
    )


def _decision_trace_counts_for_events(
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


def _posterior_staleness_alert_hours() -> float:
    """Config-driven alert threshold (ops.posterior_staleness_alert_hours)."""

    try:
        from src.config import settings

        data = getattr(settings, "_data", None)
        ops_cfg = data.get("ops") if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - config read must never block the alert.
        ops_cfg = None
    if not isinstance(ops_cfg, dict):
        return POSTERIOR_STALENESS_ALERT_HOURS_DEFAULT
    try:
        value = float(
            ops_cfg.get(
                "posterior_staleness_alert_hours",
                POSTERIOR_STALENESS_ALERT_HOURS_DEFAULT,
            )
        )
    except (TypeError, ValueError):
        return POSTERIOR_STALENESS_ALERT_HOURS_DEFAULT
    return value if value > 0.0 else POSTERIOR_STALENESS_ALERT_HOURS_DEFAULT


def _posterior_starvation_newest_blocked_reason(
    state_dir: Path,
    city: str,
    target_date: str,
    metric: str,
) -> str | None:
    key = (str(city), str(target_date), str(metric).lower())
    return _posterior_starvation_newest_blocked_reasons(
        state_dir,
        (key,),
    ).get(key)


def _posterior_starvation_newest_blocked_reasons(
    state_dir: Path,
    scopes: tuple[tuple[str, str, str], ...],
) -> dict[tuple[str, str, str], str]:
    """Read newest failure reasons for all requested scopes in one directory pass.

    Best-effort scan of the existing failed-materialization sidecar
    (config ``replacement_forecast_live.failed_dir``); never raises and never
    blocks the alert on a filesystem hiccup — this is enrichment, not the gate.
    """

    requested = frozenset(
        (str(city), str(target_date), str(metric).lower())
        for city, target_date, metric in scopes
    )
    if not requested:
        return {}
    try:
        failed_dir = state_dir / "replacement_forecast_live" / "failed"
        if not failed_dir.is_dir():
            return {}
        newest: dict[tuple[str, str, str], str] = {}
        with os.scandir(failed_dir) as entries:
            for entry in entries:
                match = _POSTERIOR_FAILED_RECEIPT_NAME.match(entry.name)
                if match is None:
                    continue
                key = (
                    match.group("city"),
                    match.group("target"),
                    match.group("metric"),
                )
                if key not in requested or entry.name <= newest.get(key, ""):
                    continue
                newest[key] = entry.name
        reasons: dict[tuple[str, str, str], str] = {}
        for key, name in newest.items():
            payload = _read_json(failed_dir / name)
            if not isinstance(payload, dict):
                continue
            reason = payload.get("stderr") or payload.get("error")
            if isinstance(reason, str) and reason.strip():
                reasons[key] = reason.strip()[:POSTERIOR_STARVATION_REASON_MAX_CHARS]
        return reasons
    except Exception:  # noqa: BLE001 - best-effort sidecar; never blocks the alert.
        return {}


def _posterior_starvation_surface(state_dir: Path, now: datetime) -> dict:
    """Alert (log-only) on a live-tradeable family with no fresh live posterior.

    Incident (2026-07-13/14): all CONUS live posteriors went dark 30-37h with
    NO operator signal. Existing watchdogs (heartbeat_supervisor, riskguard,
    the monitor-cadence watchdog in src.execution.exit_lifecycle) cover
    process heartbeat, position reference, and monitor-cadence staleness —
    none covers "a family with a live market has no fresh live posterior".
    This surface closes that gap.

    Deliberately excluded from
    ``src.engine.event_reactor_adapter._ENTRY_LIVE_HEALTH_REQUIRED_SURFACES``:
    this is an ALERT, not a new gate. The existing freshness gates already
    fail closed on the money path; this is the operator-visibility layer.

    Emits one structured ``ZEUS_POSTERIOR_STARVATION`` ERROR log line per
    starved (city, target_date, metric) scope per watchdog pass, in addition
    to the ``ok``/``issue`` fields used by the generic composite DEGRADED
    warning.
    """

    forecast_db = state_dir / "zeus-forecasts.db"
    threshold_hours = _posterior_staleness_alert_hours()

    market_columns, market_err = _sqlite_ro_table_columns(forecast_db, "market_events")
    required_market_columns = {
        "city",
        "target_date",
        "temperature_metric",
        "token_id",
        "created_at",
    }
    if market_err or not required_market_columns.issubset(market_columns):
        return {
            "ok": True,
            "issue": None,
            "evaluated": False,
            "skip_reason": market_err or "MARKET_EVENTS_COLUMNS_MISSING",
        }

    posterior_columns, posterior_err = _sqlite_ro_table_columns(
        forecast_db, "forecast_posteriors"
    )
    required_posterior_columns = {
        "city",
        "target_date",
        "temperature_metric",
        "runtime_layer",
        "computed_at",
    }
    if posterior_err or not required_posterior_columns.issubset(posterior_columns):
        return {
            "ok": True,
            "issue": None,
            "evaluated": False,
            "skip_reason": posterior_err or "FORECAST_POSTERIORS_COLUMNS_MISSING",
        }

    today_utc = now.astimezone(timezone.utc).date().isoformat()
    family_rows, family_err = _sqlite_ro_rows(
        forecast_db,
        """
        SELECT me.city AS city,
               me.target_date AS target_date,
               me.temperature_metric AS metric,
               MIN(me.created_at) AS earliest_seen_at,
               MAX(fp.computed_at) AS newest_live_posterior_at
          FROM market_events me
          LEFT JOIN forecast_posteriors fp
            ON fp.city = me.city
           AND fp.target_date = me.target_date
           AND fp.temperature_metric = me.temperature_metric
           AND fp.runtime_layer = 'live'
         WHERE me.token_id IS NOT NULL AND TRIM(me.token_id) != ''
           AND me.target_date >= ?
         GROUP BY me.city, me.target_date, me.temperature_metric
        """,
        (today_utc,),
    )
    if family_err:
        return {
            "ok": True,
            "issue": None,
            "evaluated": False,
            "skip_reason": f"POSTERIOR_STARVATION_READ_UNAVAILABLE:{family_err}",
        }

    now_utc = now.astimezone(timezone.utc)
    starved: list[dict] = []
    for row in family_rows:
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        metric = str(row.get("metric") or "")
        newest_posterior_at = _parse_iso_utc(row.get("newest_live_posterior_at"))
        if newest_posterior_at is not None:
            age_h = max(0.0, (now_utc - newest_posterior_at).total_seconds() / 3600.0)
            has_posterior = True
        else:
            earliest_seen_at = _parse_iso_utc(row.get("earliest_seen_at"))
            if earliest_seen_at is None:
                continue
            age_h = max(0.0, (now_utc - earliest_seen_at).total_seconds() / 3600.0)
            has_posterior = False
        if age_h <= threshold_hours:
            continue
        starved.append(
            {
                "city": city,
                "target_date": target_date,
                "metric": metric,
                "age_h": age_h,
                "has_posterior": has_posterior,
            }
        )

    blocked_reasons = _posterior_starvation_newest_blocked_reasons(
        state_dir,
        tuple(
            (item["city"], item["target_date"], item["metric"])
            for item in starved
        ),
    )
    for item in starved:
        city = item["city"]
        target_date = item["target_date"]
        metric = item["metric"]
        reason = blocked_reasons.get((city, target_date, metric))
        item["newest_blocked_reason"] = reason
        logger.error(
            "ZEUS_POSTERIOR_STARVATION city=%s target=%s metric=%s age_h=%.2f "
            "newest_blocked_reason=%s",
            city,
            target_date,
            metric,
            item["age_h"],
            reason or "unknown",
        )

    detail = {
        "evaluated": True,
        "threshold_hours": threshold_hours,
        "checked_family_count": len(family_rows),
        "starved_count": len(starved),
        "starved_sample": starved,
    }
    if starved:
        return {"ok": False, "issue": f"POSTERIOR_STARVATION:n={len(starved)}", **detail}
    return {"ok": True, "issue": None, **detail}


def compute_composite_live_health(
    *,
    state_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Compute and persist composite live-health status.

    Consults twenty surfaces:
      1. heartbeat — daemon-heartbeat.json (alive + fresh timestamp)
      2. venue_heartbeat — external CLOB heartbeat/order-safety keeper
      3. runtime_code — loaded_sha.json vs current git HEAD
      4. main_daemon — status/heartbeat PID still points at src.main
      5. live_trading_watchdog — watchdog status does not falsely certify loaded-only launchd
      6. live_boot_prerequisites — required sidecars are fresh; code identity is observable
      7. process_code — src.main PID start time vs live-money source mtimes
      8. run_mode  — scheduler_jobs_health.json entry for "_run_mode" job
      9. forecast_pipeline — current replacement/BPF scheduler health
      10. forecast_event_bridge — live posteriors reaching FSR event emission
      11. entry_q_version — recent entry orders retain q-authority identity
      12. pending_exit_release_loop — no repeated exit retry/reassert churn
      13. monitor_probability_freshness — active monitor probabilities are fresh
      14. sub_min_partial_position — open held shares below venue sell minimum
      15. day0_decision_trace — processed Day0 events have decision evidence
      16. forecast_decision_trace — processed forecast events have decision evidence
      17. high_yes_edge — high-confidence YES edge has action/rejection evidence
      18. status_summary — status_summary.json top-level timestamp freshness
      19. execution_capability — entry/exit side-effect gate
      20. posterior_starvation — live-tradeable family with no fresh live posterior
          (log-only alert, not a gate; see _posterior_starvation_surface)

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
            elif hb_data.get("alive") is False:
                reason = hb_data.get("failure_reason") or hb_data.get("daemon_health") or "unknown"
                hb_issue = f"NOT_ALIVE:{reason}"
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

    live_trading_watchdog_surface = _live_trading_watchdog_surface(sd, main_daemon_surface)
    surfaces["live_trading_watchdog"] = live_trading_watchdog_surface
    if not live_trading_watchdog_surface["ok"]:
        failing.append("live_trading_watchdog")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "live_trading_watchdog",
            live_trading_watchdog_surface["issue"],
        )

    live_boot_surface = _live_boot_prerequisites_surface(sd, now)
    surfaces["live_boot_prerequisites"] = live_boot_surface
    if not live_boot_surface["ok"]:
        failing.append("live_boot_prerequisites")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "live_boot_prerequisites",
            live_boot_surface["issue"],
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

    sub_min_partial_surface = _sub_min_partial_position_surface(sd, now)
    surfaces["sub_min_partial_position"] = sub_min_partial_surface
    if not sub_min_partial_surface["ok"]:
        failing.append("sub_min_partial_position")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "sub_min_partial_position",
            sub_min_partial_surface["issue"],
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

    forecast_trace_surface = _forecast_decision_trace_surface(
        sd,
        now,
        main_daemon_surface=main_daemon_surface,
    )
    surfaces["forecast_decision_trace"] = forecast_trace_surface
    if not forecast_trace_surface["ok"]:
        failing.append("forecast_decision_trace")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "forecast_decision_trace",
            forecast_trace_surface["issue"],
        )

    high_yes_surface = _high_yes_edge_missed_surface(
        sd,
        now,
        main_daemon_surface=main_daemon_surface,
    )
    surfaces["high_yes_edge"] = high_yes_surface
    if not high_yes_surface["ok"]:
        failing.append("high_yes_edge")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "high_yes_edge",
            high_yes_surface["issue"],
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

    posterior_starvation_surface = _posterior_starvation_surface(sd, now)
    surfaces["posterior_starvation"] = posterior_starvation_surface
    if not posterior_starvation_surface["ok"]:
        failing.append("posterior_starvation")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "posterior_starvation",
            posterior_starvation_surface["issue"],
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
