# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: codereview-may19-2.md relationship F
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
    command_recovery = cycle.get("command_recovery")
    chain_sync = cycle.get("chain_sync")
    progress = {
        "mode": cycle.get("mode"),
        "last_successful_cycle_at": cycle.get("completed_at") or cycle.get("started_at"),
        "candidates": candidates,
        "candidate_evaluated": candidates > 0 or "candidates" in cycle,
        "final_intents_built": final_intents,
        "final_intent_built": final_intents > 0,
        "submit_attempts": submit_attempts,
        "submit_attempted": submit_attempts > 0,
        "venue_acks": venue_acks,
        "venue_ack_observed": venue_acks > 0,
        "reconcile_progress_observed": (
            _mapping_has_positive_counter(command_recovery)
            or _mapping_has_positive_counter(chain_sync)
        ),
    }
    return {"ok": True, "issue": None, "progress": progress}


def compute_composite_live_health(
    *,
    state_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Compute and persist composite live-health status.

    Consults three surfaces:
      1. heartbeat — daemon-heartbeat.json (alive + fresh timestamp)
      2. run_mode  — scheduler_jobs_health.json entry for "_run_mode" job
      3. status_summary — status_summary.json top-level timestamp freshness

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

    # ------------------------------------------------------------------ #
    # Surface 2: run_mode (scheduler_jobs_health.json)                    #
    # ------------------------------------------------------------------ #
    sj_path = sd / "scheduler_jobs_health.json"
    sj_data = _read_json(sj_path)
    if sj_data is None:
        rm_issue = "SCHEDULER_HEALTH_MISSING"
        rm_ok = False
    else:
        # job key is "_run_mode" (the actual function name used by @_scheduler_job)
        entry = sj_data.get("_run_mode") or sj_data.get("run_mode")
        if entry is None:
            # Tolerate: scheduler_jobs_health may not have an entry yet on first
            # boot before the first cycle has run — treat as HEALTHY (no evidence
            # of failure yet).
            rm_issue = None
            rm_ok = True
        else:
            status = entry.get("status", "")
            if status == "FAILED":
                reason = entry.get("last_failure_reason") or "unknown"
                rm_issue = f"RUN_MODE_FAILED: {reason}"
                rm_ok = False
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
    # Surface 3: status_summary freshness                                 #
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
