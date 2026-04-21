"""Per-scheduler-job health artifact (B047).

Created: 2026-04-21
Last reused/audited: 2026-04-21
Authority basis: Phase 10 DT-close — docs/operations/task_2026-04-16_dual_track_metric_spine/phase10_evidence/SCAFFOLD_B047_scheduler_observability.md

Writes atomic per-job entries to ``state/scheduler_jobs_health.json``.
Separate from ``status_summary.json`` so concurrent scheduler jobs do not
stomp each other's failure signals.

Contract (B047):
  - Every scheduler.add_job(fn, ...) target in src/main.py must emit
    observable failure state via this module (usually through the
    ``@_scheduler_job`` decorator defined in src/main.py).
  - The artifact is append-only in spirit — last_run_at / last_success_at
    / last_failure_at timestamps accumulate; only the freshest per field
    is kept.
  - ``_write_heartbeat`` is exempt: it IS the coarse daemon-alive signal;
    decorating it would cause 60s-cadence file writes for no benefit.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

from src.config import state_path

logger = logging.getLogger(__name__)


_SCHEDULER_HEALTH_PATH = state_path("scheduler_jobs_health.json")


def _write_scheduler_health(
    job_name: str,
    *,
    failed: bool,
    reason: Optional[str] = None,
) -> None:
    """Atomically upsert a per-job health entry.

    On success: stamps ``last_run_at`` + ``last_success_at`` + ``status=OK``.
    On failure: stamps ``last_run_at`` + ``last_failure_at`` +
    ``last_failure_reason`` + ``status=FAILED``.

    Never raises — observability writes are best-effort and must not
    mask the primary job exception. Debug-logs on write failure.
    """
    now = datetime.now(timezone.utc).isoformat()
    path = _SCHEDULER_HEALTH_PATH
    existing: dict = {}
    if path.exists():
        try:
            with open(path) as f:
                existing = json.load(f)
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, json.JSONDecodeError):
            # Corrupt or unreadable — start fresh rather than mask.
            existing = {}

    entry = dict(existing.get(job_name) or {})
    entry["last_run_at"] = now
    if failed:
        entry["status"] = "FAILED"
        entry["last_failure_at"] = now
        entry["last_failure_reason"] = reason or ""
    else:
        entry["status"] = "OK"
        entry["last_success_at"] = now
    existing[job_name] = entry

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(existing, f, indent=2, sort_keys=True)
            os.replace(tmp, str(path))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        logger.debug(
            "failed to write scheduler health for %s", job_name, exc_info=True
        )
