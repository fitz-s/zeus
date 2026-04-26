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
- Module-level cache: posture is read once per process to avoid subprocess
  overhead per cycle. Tests must call _clear_cache() between assertions.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Grammar is frozen at these four values. Any other value in the YAML is corrupt.
POSTURE_GRAMMAR = frozenset({"NORMAL", "NO_NEW_ENTRIES", "EXIT_ONLY", "MONITOR_ONLY"})

# Module-level cache. None means not yet populated.
_cached_posture: Optional[str] = None


def _clear_cache() -> None:
    """Clear the module-level posture cache. Call in tests between assertions."""
    global _cached_posture
    _cached_posture = None


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

    Result is cached for the process lifetime. Call _clear_cache() in tests.
    """
    global _cached_posture
    if _cached_posture is not None:
        return _cached_posture

    posture = _read_posture_uncached()
    _cached_posture = posture
    return posture


def _read_posture_uncached() -> str:
    """Inner implementation — called once per process."""
    try:
        repo_root = _find_repo_root()
        posture_path = repo_root / "architecture" / "runtime_posture.yaml"
        if not posture_path.exists():
            logger.warning(
                "architecture/runtime_posture.yaml not found at %s; "
                "failing closed to NO_NEW_ENTRIES (INV-26)",
                posture_path,
            )
            return "NO_NEW_ENTRIES"

        raw = yaml.safe_load(posture_path.read_text())
        if not isinstance(raw, dict):
            logger.error(
                "runtime_posture.yaml is not a YAML mapping; failing closed to NO_NEW_ENTRIES"
            )
            return "NO_NEW_ENTRIES"

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
        return posture

    except ValueError:
        raise  # caller may propagate ValueError for corrupt grammar
    except Exception as exc:
        logger.error(
            "Failed to read runtime_posture.yaml: %s; failing closed to NO_NEW_ENTRIES",
            exc,
        )
        return "NO_NEW_ENTRIES"
