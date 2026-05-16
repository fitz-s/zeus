# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 cli/ + §3.5
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Audit-by-Grep Discipline" (lines 182-192)
"""
core/provenance — run-id generation + git commit identity + file header wrapping.

Implements the Audit-by-Grep Discipline (SAFETY_CONTRACT.md §182-192):
  - Every maintenance commit is authored by 'Maintenance Worker <maintenance@worker.local>'
  - Every commit message contains the run-id as a trailer: Run-Id: <run_id>
  - Every created file has a Generated-By header: '# Generated-By: maintenance_worker/<run_id>'

Public API:
  run_id = make_run_id() -> str
  set_commit_identity(repo_root, run_id) -> None   (context manager: restores prior identity)
  header = wrap_file_with_header(content, run_id, file_kind) -> str
  msg = make_commit_message(task_id, run_id, summary) -> str

Stdlib only. Zero Zeus identifiers.
"""
from __future__ import annotations

import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Constants — Audit-by-Grep
# ---------------------------------------------------------------------------

_COMMIT_AUTHOR_NAME = "Maintenance Worker"
_COMMIT_AUTHOR_EMAIL = "maintenance@worker.local"

# Separator between subject and trailers in commit messages.
_TRAILER_SEPARATOR = "\n\n"


# ---------------------------------------------------------------------------
# make_run_id
# ---------------------------------------------------------------------------


def make_run_id() -> str:
    """
    Return a unique, monotonically-sortable run identifier.

    Format: YYYYMMDDTHHMMSSz-<first 8 hex chars of uuid4>

    Example: 20260515T173042Z-a3f2c1b8

    The timestamp prefix enables chronological sorting. The UUID suffix
    ensures uniqueness even across rapid successive calls.
    """
    now = datetime.now(tz=timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"{ts}-{suffix}"


# ---------------------------------------------------------------------------
# set_commit_identity — context manager
# ---------------------------------------------------------------------------


def _git_config_get(repo_root: Path, key: str) -> str | None:
    """Return a git config value, or None if unset."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--local", key],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _git_config_set(repo_root: Path, key: str, value: str) -> None:
    """Set a local git config value."""
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "--local", key, value],
        capture_output=True,
        timeout=10,
        check=True,
    )


def _git_config_unset(repo_root: Path, key: str) -> None:
    """Unset a local git config value (silently ignore if already unset)."""
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "config", "--local", "--unset", key],
            capture_output=True,
            timeout=10,
            check=True,
        )
    except subprocess.CalledProcessError:
        # returncode 5 = key not set; ignore
        pass


@contextmanager
def set_commit_identity(repo_root: Path, run_id: str) -> Iterator[None]:
    """
    Context manager: set local git commit identity to Maintenance Worker.

    Captures the pre-existing user.name and user.email (if set locally),
    sets the Maintenance Worker identity, yields, then restores the prior
    values. If no prior local values existed, unsets them on exit.

    run_id is accepted (for Audit-by-Grep correlation) but not written to
    git config (identity is static; run-id appears in commit messages).

    Usage:
        with set_commit_identity(repo_root, run_id):
            subprocess.run(["git", "commit", "-m", msg], ...)
    """
    prior_name = _git_config_get(repo_root, "user.name")
    prior_email = _git_config_get(repo_root, "user.email")

    _git_config_set(repo_root, "user.name", _COMMIT_AUTHOR_NAME)
    _git_config_set(repo_root, "user.email", _COMMIT_AUTHOR_EMAIL)

    try:
        yield
    finally:
        if prior_name is not None:
            _git_config_set(repo_root, "user.name", prior_name)
        else:
            _git_config_unset(repo_root, "user.name")

        if prior_email is not None:
            _git_config_set(repo_root, "user.email", prior_email)
        else:
            _git_config_unset(repo_root, "user.email")


# ---------------------------------------------------------------------------
# wrap_file_with_header — pure function
# ---------------------------------------------------------------------------

# Comment syntax dispatch by file_kind.
# file_kind is a file extension (with or without leading dot) or a
# descriptive label ("python", "shell", "markdown", etc.).
_COMMENT_STYLES: dict[str, tuple[str, str | None]] = {
    # style: (line_prefix, block_close)
    "py":   ("# ", None),
    "sh":   ("# ", None),
    "yaml": ("# ", None),
    "yml":  ("# ", None),
    "toml": ("# ", None),
    "ini":  ("# ", None),
    "cfg":  ("# ", None),
    "ts":   ("// ", None),
    "tsx":  ("// ", None),
    "js":   ("// ", None),
    "jsx":  ("// ", None),
    "rs":   ("// ", None),
    "go":   ("// ", None),
    "java": ("// ", None),
    "md":   ("<!-- ", " -->"),
    "html": ("<!-- ", " -->"),
    "xml":  ("<!-- ", " -->"),
    # Descriptive aliases
    "python":   ("# ", None),
    "shell":    ("# ", None),
    "markdown": ("<!-- ", " -->"),
    "diff":     ("# ", None),
    "tsv":      ("# ", None),
    "json":     (None, None),  # JSON has no comments — skip header silently
}


def wrap_file_with_header(content: str, run_id: str, file_kind: str) -> str:
    """
    Return content with a Generated-By provenance header prepended.

    file_kind: file extension (with or without leading dot) or descriptive
               label. Case-insensitive. Unknown kinds default to '#' prefix.

    Header format (for # prefix):
        # Generated-By: maintenance_worker/<run_id>

    The header is separated from the content body by a blank line.
    JSON files have no comment syntax — content is returned unchanged.

    Pure function: does not touch the filesystem.
    """
    kind = file_kind.lstrip(".").lower()
    style = _COMMENT_STYLES.get(kind)

    if style is None:
        # Unknown kind — default to '#' prefix (fail-safe)
        prefix, close = ("# ", None)
    else:
        prefix, close = style

    if prefix is None:
        # JSON or other formats with no comment support — return unchanged
        return content

    generated_by = f"maintenance_worker/{run_id}"

    if close is not None:
        # Block comment style (e.g. HTML)
        header_line = f"{prefix}Generated-By: {generated_by}{close}"
    else:
        header_line = f"{prefix}Generated-By: {generated_by}"

    return header_line + "\n\n" + content


# ---------------------------------------------------------------------------
# make_commit_message
# ---------------------------------------------------------------------------


def make_commit_message(task_id: str, run_id: str, summary: str) -> str:
    """
    Build a maintenance commit message with Audit-by-Grep trailers.

    Format:
        maint(<task_id>): <summary>

        Run-Id: <run_id>

    SAFETY_CONTRACT.md §182-192: every commit must carry the Run-Id so
    that `git log --author='Maintenance Worker' --pretty='%h %ai %s'`
    returns an auditable trail and each run-id maps to an evidence trail.
    """
    subject = f"maint({task_id}): {summary}"
    trailers = f"Run-Id: {run_id}"
    return f"{subject}{_TRAILER_SEPARATOR}{trailers}"
