# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §launchagent_backup_quarantine
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md
#   §category-1-launchagent-backup-shrapnel
"""
Handler: launchagent_backup_quarantine

Proposes quarantine of stale LaunchAgent backup/shrapnel files.

enumerate(): walks ~/Library/LaunchAgents/ for files matching the backup
  regex pattern (*.bak, *.backup, *.replaced, *.locked, *.before_*);
  filters by mtime > backup_ttl_days (default 14); pre_check: a
  corresponding active (non-backup) .plist must exist alongside the
  backup file.

apply(): always dry_run_only (live_default: false in catalog). Returns
  mock diff showing planned move to quarantine_dir.

Verdict strings:
  LAUNCHAGENT_BACKUP_CANDIDATE  — stale backup file with active plist
  SKIP_NO_ACTIVE_PLIST          — no corresponding active .plist found
  SKIP_TOO_FRESH                — mtime within backup_ttl_days
  SKIP_FORBIDDEN                — matches forbidden path pattern
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import TaskSpec, TickContext

logger = logging.getLogger(__name__)

# Verdict strings
VERDICT_CANDIDATE = "LAUNCHAGENT_BACKUP_CANDIDATE"
VERDICT_SKIP_NO_ACTIVE_PLIST = "SKIP_NO_ACTIVE_PLIST"
VERDICT_SKIP_FRESH = "SKIP_TOO_FRESH"
VERDICT_SKIP_FORBIDDEN = "SKIP_FORBIDDEN"

DEFAULT_TTL_DAYS = 14
DEFAULT_QUARANTINE_DIR = "~/Library/LaunchAgents/.archive"
DEFAULT_QUARANTINE_RETENTION_DAYS = 90
# Catalog regex: files matching backup/shrapnel suffixes
DEFAULT_BACKUP_REGEX = r"\.(bak|backup|replaced|locked|before_[a-z_]+)[-._]?[0-9TZ_:-]*(?:\.bak)?$"
# Forbidden: active (non-backup) com.zeus.* plists must never be quarantined
DEFAULT_FORBIDDEN_PATTERN = r"com\.zeus\.[^.]+\.plist$"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate(entry: Any, ctx: TickContext) -> list[Candidate]:  # noqa: A001
    """
    Walk ~/Library/LaunchAgents/ for stale backup/shrapnel files.

    Forbidden: active com.zeus.* plists (non-backup form).
    pre_check: corresponding active .plist must exist.

    Returns list[Candidate] with LAUNCHAGENT_BACKUP_CANDIDATE or SKIP_*.
    """
    raw: dict = entry.raw
    config: dict = raw.get("config", {})

    ttl_days: int = int(config.get("backup_ttl_days", DEFAULT_TTL_DAYS))
    quarantine_dir_str: str = config.get("quarantine_dir", DEFAULT_QUARANTINE_DIR)
    backup_regex_str: str = config.get("regex", DEFAULT_BACKUP_REGEX)

    try:
        backup_re = re.compile(backup_regex_str)
    except re.error as exc:
        logger.warning("launchagent_backup_quarantine: invalid regex %r: %s", backup_regex_str, exc)
        backup_re = re.compile(DEFAULT_BACKUP_REGEX)

    launch_agents_dir = _get_launch_agents_dir()
    now_ts = time.time()
    ttl_seconds = ttl_days * 86400

    candidates: list[Candidate] = []

    if not launch_agents_dir.exists():
        logger.debug("launchagent_backup_quarantine: LaunchAgents dir missing: %s", launch_agents_dir)
        return candidates

    for path in sorted(launch_agents_dir.iterdir()):
        if not path.is_file():
            continue

        name = path.name

        # Match backup regex against filename
        if not backup_re.search(name):
            continue

        # Forbidden pattern check (active zeus plists — not backup form)
        if _is_forbidden(name):
            candidates.append(Candidate(
                task_id="launchagent_backup_quarantine",
                path=path,
                verdict=VERDICT_SKIP_FORBIDDEN,
                reason=f"matches forbidden active-plist pattern: {name}",
                evidence={"name": name},
            ))
            continue

        # mtime check
        mtime = _get_mtime(path)
        if mtime is None or (now_ts - mtime) < ttl_seconds:
            age_days = round((now_ts - mtime) / 86400, 1) if mtime is not None else None
            candidates.append(Candidate(
                task_id="launchagent_backup_quarantine",
                path=path,
                verdict=VERDICT_SKIP_FRESH,
                reason=f"backup file too fresh (age={age_days}d < {ttl_days}d)",
                evidence={"name": name, "age_days": age_days, "ttl_days": ttl_days},
            ))
            continue

        # pre_check: corresponding_active_plist_must_exist
        active_plist = _find_active_plist(path, launch_agents_dir)
        if active_plist is None:
            candidates.append(Candidate(
                task_id="launchagent_backup_quarantine",
                path=path,
                verdict=VERDICT_SKIP_NO_ACTIVE_PLIST,
                reason="no corresponding active .plist found alongside backup",
                evidence={"name": name, "searched_dir": str(launch_agents_dir)},
            ))
            continue

        age_days = round((now_ts - mtime) / 86400, 1)
        candidates.append(Candidate(
            task_id="launchagent_backup_quarantine",
            path=path,
            verdict=VERDICT_CANDIDATE,
            reason=f"stale backup file: age={age_days}d > ttl={ttl_days}d; active plist exists",
            evidence={
                "name": name,
                "age_days": age_days,
                "ttl_days": ttl_days,
                "active_plist": str(active_plist),
            },
        ))

    return candidates


def apply(decision: Candidate, ctx: TickContext) -> ApplyResult:
    """
    Apply LaunchAgent backup quarantine. Always dry_run_only (live_default: false).

    TOP-OF-FUNCTION GUARD per PLAN §1.5.4 — defense-in-depth.
    Returns ApplyResult with mock diff showing planned move to quarantine_dir.
    """
    # This task is ALWAYS proposal-only — dry_run_only unconditionally.
    mock = _mock_diff(decision)
    return ApplyResult(
        task_id="launchagent_backup_quarantine",
        dry_run_only=True,
        diff=mock,
    )


# ---------------------------------------------------------------------------
# Internal helpers (mockable)
# ---------------------------------------------------------------------------


def _get_launch_agents_dir() -> Path:
    """Return the path to ~/Library/LaunchAgents/."""
    return Path.home() / "Library" / "LaunchAgents"


def _is_forbidden(name: str) -> bool:
    """
    Return True if name matches the forbidden active-plist pattern.

    Forbidden: com.zeus.*.plist in non-backup form (exact .plist extension,
    no backup suffix embedded). Backup forms like com.zeus.monitor.plist.bak
    are already caught by the backup regex before this check.
    """
    # A name is forbidden (active plist) if it ends with .plist AND does not
    # contain any backup suffix — but since we only process backup_re matches,
    # this guard is defense-in-depth for edge cases.
    forbidden_re = re.compile(r"^com\.zeus\.[^.]+\.plist$")
    return bool(forbidden_re.match(name))


def _find_active_plist(backup_path: Path, launch_agents_dir: Path) -> Path | None:
    """
    Find the corresponding active (non-backup) .plist for a backup file.

    Strategy: strip all backup suffixes from the filename to derive the
    expected active plist name, then check if it exists.

    Examples:
      com.zeus.monitor.plist.bak → com.zeus.monitor.plist
      com.openclaw.agent.replaced → com.openclaw.agent (no plist → try + .plist)
      com.openclaw.agent.before_update → com.openclaw.agent.plist
    """
    name = backup_path.name
    # Strip common backup suffixes iteratively
    backup_suffix_re = re.compile(
        r"\.(bak|backup|replaced|locked|before_[a-z_]+)[-._]?[0-9TZ]*(?:\.bak)?$"
    )
    base = backup_suffix_re.sub("", name)

    # Try exact base, then base + .plist
    for candidate_name in [base, base + ".plist"]:
        candidate = launch_agents_dir / candidate_name
        if candidate.exists() and candidate != backup_path:
            return candidate

    return None


def _get_mtime(path: Path) -> float | None:
    """Return mtime of path in seconds since epoch, or None if inaccessible."""
    try:
        return path.stat().st_mtime
    except (OSError, FileNotFoundError):
        return None


def _mock_diff(decision: Candidate) -> tuple[str, ...]:
    """Return a mock diff string showing planned move to quarantine dir."""
    return (
        f"[DRY RUN] would move: {decision.path}",
        f"       → ~/Library/LaunchAgents/.archive/{decision.path.name}",
        f"  reason: {decision.reason}",
    )
