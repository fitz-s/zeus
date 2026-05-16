# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md §5, §5.1, §5.2
"""
Companion skip-token usage logger for topology v_next P2.1.

Public API:
    write_skip_event(
        profile, source_files, expected_companions, token_value,
        justification=None, false_positive_claim=False,
    ) -> None

Properties:
- Append-only JSONL at state/companion_skip_token_log.jsonl.
- Atomic write: write to .tmp then os.rename (same pattern as divergence_logger.py).
- agent_id resolved from environment at write time:
    OMC_AGENT_ID → CLAUDE_AGENT_ID → CODEX_AGENT_ID → "unknown"
- session_id resolved from CLAUDE_SESSION_ID env var; null when absent.
- justification_env: COMPANION_SKIP_JUSTIFICATION env var value, null when absent.
- false_positive_claim: when True, records event_type="false_positive_claim".
- Codex-importable: stdlib only (json, os, time, datetime, pathlib).
- Log path overridable via COMPANION_SKIP_LOG_PATH env var for tests.
"""
from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_LOG_PATH = "state/companion_skip_token_log.jsonl"
_LOG_PATH_ENV_VAR = "COMPANION_SKIP_LOG_PATH"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_skip_event(
    profile: str,
    source_files: list[str],
    expected_companions: list[str],
    token_value: str,
    justification: str | None = None,
    false_positive_claim: bool = False,
) -> None:
    """
    Append a skip-token usage record to the companion skip log (SCAFFOLD §5).

    Parameters
    ----------
    profile:
        The profile_id for which the skip token was honored.
    source_files:
        Verbatim copy of the `files` argument passed to `admit()`.
    expected_companions:
        The authority-doc paths that were skipped (binding.companion_required[profile]).
    token_value:
        The full token string from the binding (e.g. "COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1").
        Logged verbatim so the digest shows WHICH token was honored.
    justification:
        Optional one-line reason string (override via COMPANION_SKIP_JUSTIFICATION env).
        When None, the env var is checked; if set, its value is used.
    false_positive_claim:
        When True, sets event_type="false_positive_claim" in the record (SCAFFOLD §9).
        Used when the agent believes the MISSING_COMPANION emission was incorrect.

    Notes
    -----
    - Atomic write: log to .tmp file then os.rename (POSIX atomic on same fs).
    - Directory is created if it does not exist.
    - On write failure, the error is suppressed (admission must not fail because
      the log is unavailable).
    - agent_id resolved from env at write time per SCAFFOLD §0 INCONSISTENCY-3.
    """
    try:
        _write_skip_event_impl(
            profile=profile,
            source_files=source_files,
            expected_companions=expected_companions,
            token_value=token_value,
            justification=justification,
            false_positive_claim=false_positive_claim,
        )
    except Exception:
        # Suppress all errors — log write failure must NOT fail admission.
        pass


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------

def _write_skip_event_impl(
    profile: str,
    source_files: list[str],
    expected_companions: list[str],
    token_value: str,
    justification: str | None,
    false_positive_claim: bool,
) -> None:
    """Core implementation (not exception-shielded; called by write_skip_event)."""
    log_path = Path(os.environ.get(_LOG_PATH_ENV_VAR, _DEFAULT_LOG_PATH))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    record = _build_record(
        profile=profile,
        source_files=source_files,
        expected_companions=expected_companions,
        token_value=token_value,
        justification=justification,
        false_positive_claim=false_positive_claim,
    )
    record_line = json.dumps(record, separators=(",", ":")) + "\n"

    _atomic_append(log_path, record_line)


def _build_record(
    profile: str,
    source_files: list[str],
    expected_companions: list[str],
    token_value: str,
    justification: str | None,
    false_positive_claim: bool,
) -> dict[str, Any]:
    """
    Build the JSONL record dict (SCAFFOLD §5.2 schema).

    Schema fields:
    - ts: ISO-8601 UTC timestamp
    - event_type: "skip_token_used" or "false_positive_claim"
    - profile: exact profile_id
    - source_files: list of submitted file paths
    - expected_companions: list of authority-doc paths that were skipped
    - token_value: full token string from binding
    - agent_id: resolved from env at write time
    - session_id: CLAUDE_SESSION_ID env var or null
    - justification_env: COMPANION_SKIP_JUSTIFICATION env var or caller-supplied value
    """
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    event_type = "false_positive_claim" if false_positive_claim else "skip_token_used"

    # Resolve agent_id from env (SCAFFOLD §0 INCONSISTENCY-3 fallback chain)
    agent_id = (
        os.environ.get("OMC_AGENT_ID")
        or os.environ.get("CLAUDE_AGENT_ID")
        or os.environ.get("CODEX_AGENT_ID")
        or "unknown"
    )

    # session_id: best-effort env read; null acceptable
    session_id = os.environ.get("CLAUDE_SESSION_ID") or None

    # justification: caller override takes precedence; else env var
    resolved_justification = justification or os.environ.get("COMPANION_SKIP_JUSTIFICATION") or None

    return {
        "ts": ts,
        "event_type": event_type,
        "profile": profile,
        "source_files": list(source_files),
        "expected_companions": list(expected_companions),
        "token_value": token_value,
        "agent_id": agent_id,
        "session_id": session_id,
        "justification_env": resolved_justification,
    }


def _atomic_append(log_path: Path, record_line: str) -> None:
    """
    Atomically append *record_line* to *log_path*.

    Strategy: read existing content, append new line, write to .tmp, os.rename.
    This mirrors the pattern used by divergence_logger.py for POSIX atomicity.

    Note: True atomic append to JSONL requires a read-modify-write cycle on
    filesystems that don't support O_APPEND atomicity for multi-line content.
    For single-line records, direct append mode suffices on local filesystems.
    """
    tmp_path = log_path.with_suffix(log_path.suffix + ".tmp")

    # Read existing content if present
    if log_path.exists():
        existing = log_path.read_bytes()
    else:
        existing = b""

    new_content = existing + record_line.encode("utf-8")

    tmp_path.write_bytes(new_content)
    os.rename(tmp_path, log_path)
