# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Refusal Modes"
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Kill Switch"
"""
refusal — refuse_fatal() and skip_tick() refusal modes.

refuse_fatal(): hard refusal, exits non-zero with a unique exit code per
  RefusalReason. Writes a tick-skipped row to errors.tsv for audit.
  Does NOT write SELF_QUARANTINE (Path A per SCAFFOLD §3 lines 190-195).
  Human must intervene to resume.

skip_tick(): soft refusal, logs the skip and returns. Next tick retries
  automatically without human intervention (MAINTENANCE_PAUSED, ONCALL_QUIET).

Exit codes: RefusalReason enum ordinal + 10 to avoid clash with OS codes.
  Ordinal 0 → exit code 10, ordinal 1 → 11, etc. Each RefusalReason has
  a unique code per SCAFFOLD §3 refusal.py contract.

SCAFFOLD §6 verdict:
  6 refuse_fatal hard guards: KILL_SWITCH, DIRTY_REPO, ACTIVE_REBASE,
    LOW_DISK, INFLIGHT_PR, SELF_QUARANTINED
  2 skip_tick soft guards: MAINTENANCE_PAUSED, ONCALL_QUIET

Stdlib only. Imports only from maintenance_worker.types.*.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

from maintenance_worker.types.modes import RefusalReason
from maintenance_worker.types.specs import TickContext

logger = logging.getLogger(__name__)

# Base offset so exit codes don't collide with reserved OS codes (0-9).
_EXIT_CODE_BASE = 10

# Hard guards that map to refuse_fatal — listed for exhaustive verification.
_REFUSE_FATAL_REASONS: frozenset[RefusalReason] = frozenset(
    {
        RefusalReason.KILL_SWITCH,
        RefusalReason.DIRTY_REPO,
        RefusalReason.ACTIVE_REBASE,
        RefusalReason.LOW_DISK,
        RefusalReason.INFLIGHT_PR,
        RefusalReason.SELF_QUARANTINED,
        RefusalReason.FORBIDDEN_PATH_VIOLATION,
        RefusalReason.FORBIDDEN_OPERATION_VIOLATION,
    }
)

# Soft guards that map to skip_tick.
_SKIP_TICK_REASONS: frozenset[RefusalReason] = frozenset(
    {
        RefusalReason.MAINTENANCE_PAUSED,
        RefusalReason.ONCALL_QUIET,
    }
)


def _exit_code_for(reason: RefusalReason) -> int:
    """Unique exit code per RefusalReason (ordinal + _EXIT_CODE_BASE)."""
    members = list(RefusalReason)
    return members.index(reason) + _EXIT_CODE_BASE


def _write_errors_tsv(ctx: TickContext, reason: RefusalReason, message: str) -> None:
    """
    Append a refusal row to evidence_trail/errors.tsv.

    Non-fatal: if the evidence dir is not writable, logs a warning and
    continues to the sys.exit call. The write must not mask the refusal.
    """
    try:
        evidence_dir = ctx.config.evidence_dir
        evidence_dir.mkdir(parents=True, exist_ok=True)
        errors_tsv = evidence_dir / "errors.tsv"
        timestamp = datetime.now(tz=timezone.utc).isoformat()
        row = f"{timestamp}\t{ctx.run_id}\t{reason.value}\t{message}\n"
        with errors_tsv.open("a", encoding="utf-8") as fh:
            fh.write(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write errors.tsv: %s", exc)


def refuse_fatal(reason: RefusalReason, ctx: TickContext, message: str = "") -> NoReturn:
    """
    Hard refusal — exits non-zero. Human must intervene.

    Path A per SCAFFOLD §3: does NOT write SELF_QUARANTINE.
    Only post_mutation_detector() (Path B) writes the quarantine file.

    Writes a row to errors.tsv, logs the reason, then sys.exit(code).
    """
    detail = message or reason.value
    logger.error(
        "TICK REFUSED [%s]: run_id=%s reason=%s %s",
        "refuse_fatal",
        ctx.run_id,
        reason.value,
        detail,
    )
    _write_errors_tsv(ctx, reason, detail)
    code = _exit_code_for(reason)
    sys.exit(code)


def skip_tick(reason: RefusalReason, ctx: TickContext, message: str = "") -> None:
    """
    Soft refusal — skips this tick, returns normally. Next tick retries.

    Per SAFETY_CONTRACT.md lines 207-213: MAINTENANCE_PAUSED "skips ticks
    but does not require a re-acknowledge to resume."

    Writes a row to errors.tsv for audit visibility then returns.
    Does NOT exit non-zero (caller returns normally after this call).
    """
    detail = message or reason.value
    logger.info(
        "TICK SKIPPED [%s]: run_id=%s reason=%s %s",
        "skip_tick",
        ctx.run_id,
        reason.value,
        detail,
    )
    _write_errors_tsv(ctx, reason, detail)
    # Returns normally — caller exits 0 after skip.
