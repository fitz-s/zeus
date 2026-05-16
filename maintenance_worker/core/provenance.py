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

import os
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


@contextmanager
def set_commit_identity(repo_root: Path, run_id: str) -> Iterator[None]:
    """
    Context manager: inject Maintenance Worker identity via environment variables.

    Sets GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL, GIT_COMMITTER_NAME,
    GIT_COMMITTER_EMAIL in the current process environment for the duration
    of the context, then restores prior values on exit.

    This approach is SIGKILL-safe: no persistent git config mutation occurs.
    The prior implementation mutated local git config, which left the repo
    in a contaminated state if the process was killed mid-tick (SEV-2 #4).

    run_id is accepted for Audit-by-Grep correlation; the Run-Id appears in
    commit messages via make_commit_message(), not in the identity.

    Usage:
        with set_commit_identity(repo_root, run_id):
            subprocess.run(["git", "-C", str(repo_root), "commit", "-m", msg], ...)
    """
    _env_keys = (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    )
    prior: dict[str, str | None] = {k: os.environ.get(k) for k in _env_keys}

    os.environ["GIT_AUTHOR_NAME"] = _COMMIT_AUTHOR_NAME
    os.environ["GIT_AUTHOR_EMAIL"] = _COMMIT_AUTHOR_EMAIL
    os.environ["GIT_COMMITTER_NAME"] = _COMMIT_AUTHOR_NAME
    os.environ["GIT_COMMITTER_EMAIL"] = _COMMIT_AUTHOR_EMAIL

    try:
        yield
    finally:
        for key in _env_keys:
            if prior[key] is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior[key]  # type: ignore[assignment]


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
