# Created: 2026-05-19
# Last reused or audited: 2026-06-17
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
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATUS_FRESH_BUDGET_SECONDS = 300  # 5 minutes — consistent with heartbeat budget


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
    if candidates > 0 and final_intents <= 0 and not no_trade_reason_proof:
        return {
            "ok": False,
            "issue": "CANDIDATES_WITHOUT_FINAL_INTENTS_OR_NO_TRADE_REASONS",
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
                "blocked_components": [f"{action_name}_capability_missing"],
                "blocked_reasons": [],
            }
            blocked.append(f"{action_name}:missing")
            continue

        status = str(action.get("status") or "unknown")
        allow_submit = action.get("global_allow_submit")
        blocked_components = action.get("blocked_components")
        if not isinstance(blocked_components, list):
            blocked_components = []
        blocked_reasons = []
        for component in action.get("components") or []:
            if not isinstance(component, dict):
                continue
            if component.get("allowed") is False:
                blocked_reasons.append(
                    {
                        "component": component.get("component"),
                        "reason": component.get("reason"),
                    }
                )

        action_blocked = (
            status.lower() in {"blocked", "failed", "error"}
            or allow_submit is False
            or bool(blocked_components)
            or bool(blocked_reasons)
        )
        actions[action_name] = {
            "status": status,
            "global_allow_submit": allow_submit,
            "blocked_components": blocked_components,
            "blocked_reasons": blocked_reasons,
        }
        if action_blocked:
            component_label = ",".join(str(c) for c in blocked_components) or status
            blocked.append(f"{action_name}:{component_label}")

    if blocked:
        return {
            "ok": False,
            "issue": "LIVE_EXECUTION_CAPABILITY_BLOCKED: " + "; ".join(blocked),
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


def compute_composite_live_health(
    *,
    state_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Compute and persist composite live-health status.

    Consults five surfaces:
      1. heartbeat — daemon-heartbeat.json (alive + fresh timestamp)
      2. venue_heartbeat — external CLOB heartbeat/order-safety keeper
      3. run_mode  — scheduler_jobs_health.json entry for "_run_mode" job
      4. status_summary — status_summary.json top-level timestamp freshness
      5. execution_capability — entry/exit side-effect gate

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

    venue_surface = _venue_heartbeat_surface(sd, now)
    surfaces["venue_heartbeat"] = venue_surface
    if not venue_surface["ok"]:
        failing.append("venue_heartbeat")
        logger.warning(
            "live_health_composite DEGRADED: failing_surface=%s reason=%s",
            "venue_heartbeat",
            venue_surface["issue"],
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
        entries = []
        for key, value in sj_data.items():
            if key in {"_run_mode", "run_mode"} or str(key).startswith("run_mode:"):
                if isinstance(value, dict):
                    entries.append((str(key), value))
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
    # Surface 4: status_summary freshness                                 #
    # ------------------------------------------------------------------ #
    ss_path = sd / "status_summary.json"
    ss_data = _read_json(ss_path)
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
                ss_issue = None
                ss_ok = True

    surfaces["status_summary"] = {"ok": ss_ok, "issue": ss_issue}
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
