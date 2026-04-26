# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/decisions.md §O2-c
"""Runtime posture reader — INV-26.

Reads architecture/runtime_posture.yaml (a committed YAML law document, O2-c)
to determine the current branch posture. The posture gate is additive with
risk_level: both must allow entry for any new position to be created.

Design decisions (from decisions.md §O2-c):
- No runtime override path. The YAML is read-only at runtime.
- Branch lookup: resolve current git branch, look up in `branches:` dict.
  Falls back to `default_posture` if branch is not listed.
- Fail-closed: any read error, YAML parse error, or missing file returns
  NO_NEW_ENTRIES (never NORMAL).
- Module-level cache: posture is read at most once per 60 seconds, with
  invalidation on yaml mtime change or branch change so long-running
  daemons (zeus monitor, riskguard) pick up operator YAML commits and
  branch checkouts without restart. Tests must call _clear_cache()
  between assertions.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Grammar is frozen at these four values. Any other value in the YAML is corrupt.
POSTURE_GRAMMAR = frozenset({"NORMAL", "NO_NEW_ENTRIES", "EXIT_ONLY", "MONITOR_ONLY"})

# Cache TTL: re-read the YAML at most every 60 seconds so long-running daemons
# pick up operator commits to architecture/runtime_posture.yaml without restart.
_CACHE_TTL_SECONDS = 60

# Module-level cache entry: (posture_value, branch_name, yaml_mtime, cached_at_ts)
# None means not yet populated.
_cache_entry: Optional[tuple[str, str, float, float]] = None


def _clear_cache() -> None:
    """Clear the module-level posture cache. Call in tests between assertions."""
    global _cache_entry
    _cache_entry = None


def _cache_is_valid(entry: tuple[str, str, float, float]) -> bool:
    """Return True if the cached entry is still valid.

    Invalidation triggers (any one is sufficient):
    - cached_at_ts is older than _CACHE_TTL_SECONDS
    - yaml_mtime differs from the file's current mtime
    - current branch name differs from the cached branch
    """
    posture_val, branch_name, yaml_mtime, cached_at_ts = entry
    if time.monotonic() - cached_at_ts >= _CACHE_TTL_SECONDS:
        return False
    try:
        repo_root = _find_repo_root()
        posture_path = repo_root / "architecture" / "runtime_posture.yaml"
        if posture_path.exists() and posture_path.stat().st_mtime != yaml_mtime:
            return False
    except Exception:
        return False
    current_branch = _resolve_current_branch()
    if current_branch != branch_name:
        return False
    return True


def _find_repo_root() -> Path:
    """Locate the repo root by walking up from this module's location.

    Uses the presence of architecture/runtime_posture.yaml as the anchor.
    Falls back to walking up until finding a .git directory or reaching
    the filesystem root.
    """
    candidate = Path(__file__).resolve().parent
    for _ in range(20):  # max 20 levels up
        if (candidate / "architecture" / "runtime_posture.yaml").exists():
            return candidate
        if (candidate / ".git").exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    # Last resort: use the module's parent chain
    return Path(__file__).resolve().parents[2]


def _resolve_current_branch() -> str:
    """Resolve current git branch name via subprocess.

    Returns the branch name string, or "UNKNOWN" on any failure.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                return branch
        return "UNKNOWN"
    except Exception as exc:
        logger.debug("Could not resolve git branch: %s", exc)
        return "UNKNOWN"


def read_runtime_posture() -> str:
    """Read and return the current runtime posture string (INV-26).

    Returns one of: NORMAL | NO_NEW_ENTRIES | EXIT_ONLY | MONITOR_ONLY.
    Fail-closed: returns NO_NEW_ENTRIES on any error.

    Cache policy (TTL + mtime + branch invalidation):
    - Re-reads the YAML when cached_at_ts is older than _CACHE_TTL_SECONDS (60s),
      when the YAML file's mtime changes, or when the git branch changes.
    - This ensures long-running daemons (zeus monitor, riskguard) pick up
      operator commits to architecture/runtime_posture.yaml without restart.
    - Call _clear_cache() in tests between assertions.
    """
    global _cache_entry
    if _cache_entry is not None and _cache_is_valid(_cache_entry):
        return _cache_entry[0]

    posture, branch, yaml_mtime = _read_posture_uncached()
    _cache_entry = (posture, branch, yaml_mtime, time.monotonic())
    return posture


def _read_posture_uncached() -> tuple[str, str, float]:
    """Inner implementation — returns (posture, branch, yaml_mtime).

    yaml_mtime is 0.0 if the file is absent (fail-closed path).
    """
    try:
        repo_root = _find_repo_root()
        posture_path = repo_root / "architecture" / "runtime_posture.yaml"
        if not posture_path.exists():
            logger.warning(
                "architecture/runtime_posture.yaml not found at %s; "
                "failing closed to NO_NEW_ENTRIES (INV-26)",
                posture_path,
            )
            branch = _resolve_current_branch()
            return "NO_NEW_ENTRIES", branch, 0.0

        yaml_mtime = posture_path.stat().st_mtime
        raw = yaml.safe_load(posture_path.read_text())
        if not isinstance(raw, dict):
            logger.error(
                "runtime_posture.yaml is not a YAML mapping; failing closed to NO_NEW_ENTRIES"
            )
            branch = _resolve_current_branch()
            return "NO_NEW_ENTRIES", branch, yaml_mtime

        grammar = raw.get("posture_grammar")
        if isinstance(grammar, list):
            valid = frozenset(grammar)
        else:
            valid = POSTURE_GRAMMAR

        default_posture = raw.get("default_posture", "NO_NEW_ENTRIES")
        if default_posture not in valid:
            raise ValueError(
                f"runtime_posture.yaml default_posture={default_posture!r} is not in grammar {sorted(valid)}"
            )

        branch = _resolve_current_branch()
        branches = raw.get("branches", {})
        posture = branches.get(branch, default_posture)

        if posture not in valid:
            raise ValueError(
                f"runtime_posture.yaml branch={branch!r} posture={posture!r} is not in grammar {sorted(valid)}"
            )

        logger.debug("Runtime posture: branch=%r posture=%r", branch, posture)
        return posture, branch, yaml_mtime

    except ValueError:
        raise  # caller may propagate ValueError for corrupt grammar
    except Exception as exc:
        logger.error(
            "Failed to read runtime_posture.yaml: %s; failing closed to NO_NEW_ENTRIES",
            exc,
        )
        branch = _resolve_current_branch()
        return "NO_NEW_ENTRIES", branch, 0.0
