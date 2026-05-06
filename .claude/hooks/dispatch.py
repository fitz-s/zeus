#!/usr/bin/env python3
# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: PLAN §2.4 + critic-opus §0.5 (ATTACK 4, 7, 8 binding)
#   evidence/hook_redesign_critic_opus.md
#   docs/operations/task_2026-05-06_hook_redesign/PLAN.md

"""
dispatch.py — single entry point for every Claude Code hook event.

Phase 1 (parallel-install): YAML schema loaded; signal emitted; advisory/blocking
semantics enforced per critic-opus §0.5. Actual check logic for each hook_id is
stub-only in Phase 1; full implementations land in Phase 2.

Crash semantics (ATTACK 8 / OD-HOOK-1):
  - ADVISORY hooks: fail-open (exit 0) on any unhandled exception.
  - BLOCKING hooks: fail-closed (exit 2 + stderr message) on any unhandled exception.

Override expiry (ATTACK 7):
  - Clock-start = git log -1 --format=%ct <evidence_file>  (immutable provenance).
  - Replay protection: (override_id, evidence_file) pair counts once per 30d window.
  - auto_expires_after: never  permitted only for REVIEW_SAFE_TAG + ISOLATED_WORKTREE.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / ".claude" / "hooks" / "registry.yaml"
OVERRIDES_PATH = REPO_ROOT / ".claude" / "hooks" / "overrides.yaml"
LOG_DIR = REPO_ROOT / ".claude" / "logs" / "hook_signal"
OVERRIDE_LOG_DIR = REPO_ROOT / ".claude" / "logs" / "hook_overrides"

# override_ids that may carry auto_expires_after: never
_NEVER_EXPIRY_WHITELIST = {"REVIEW_SAFE_TAG", "ISOLATED_WORKTREE"}

# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML not installed; cannot load hook registry.")
    return yaml.safe_load(path.read_text())


def _load_registry() -> dict[str, Any]:
    return _load_yaml(REGISTRY_PATH)


def _load_overrides() -> dict[str, Any]:
    return _load_yaml(OVERRIDES_PATH)


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def _emit_signal(
    hook_id: str,
    event: str,
    decision: str,
    reason: str,
    payload: dict[str, Any],
    *,
    override_id: str | None = None,
) -> None:
    """Write one telemetry line to .claude/logs/hook_signal/<YYYY-MM>.jsonl."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    log_file = LOG_DIR / f"{month}.jsonl"
    entry = {
        "hook_id": hook_id,
        "event": event,
        "decision": decision,
        "reason": reason,
        "override_id": override_id,
        "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id"),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with log_file.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Override validation (ATTACK 7)
# ---------------------------------------------------------------------------

# In-process replay-protection cache: {(override_id, evidence_file) -> first_seen_ts}
# This is per-process; durable replay protection would use the audit jsonl.
_SEEN_PAIRS: dict[tuple[str, str | None], float] = {}


def _parse_duration_to_seconds(value: str) -> float | None:
    """Parse '24h', '7d', '5m', '1h', '60s' into seconds. Returns None for 'never'."""
    if value == "never":
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = value[-1]
    if unit not in units:
        raise ValueError(f"Unknown duration unit: {value!r}")
    return float(value[:-1]) * units[unit]


def _get_evidence_file_commit_time(evidence_file_path: Path) -> float | None:
    """Clock-start = git log -1 --format=%ct <path> (immutable provenance)."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", str(evidence_file_path)],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=REPO_ROOT,
        )
        ct = result.stdout.strip()
        if ct:
            return float(ct)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def validate_override(
    override: dict[str, Any],
    spec: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    """
    Return True if the structured override is valid for this invocation.

    Checks:
    1. auto_expires_after: never only permitted for REVIEW_SAFE_TAG + ISOLATED_WORKTREE.
    2. Evidence file exists (when requires.evidence_file is set).
    3. Override not expired (clock-start = git commit timestamp of evidence file).
    4. Replay protection: (override_id, evidence_file) pair counts once per 30d.
    """
    override_id: str = override["id"]
    requires: dict[str, Any] = override.get("requires", {})
    auto_expires: str = requires.get("auto_expires_after", "24h")

    # Rule 1: 'never' whitelist
    if auto_expires == "never" and override_id not in _NEVER_EXPIRY_WHITELIST:
        _emit_signal(
            override_id,
            spec.get("event", ""),
            "deny",
            f"auto_expires_after:never not permitted for {override_id}",
            payload,
            override_id=override_id,
        )
        return False

    # Rule 2: evidence file existence
    evidence_rel: str | None = requires.get("evidence_file")
    evidence_path: Path | None = None
    if evidence_rel:
        # Replace template placeholders with a wildcard-style check
        if "<" in evidence_rel:
            # Template path — cannot validate existence without concrete date/id;
            # Phase 2 will fill in concrete resolution. In Phase 1, accept.
            pass
        else:
            evidence_path = REPO_ROOT / evidence_rel
            if not evidence_path.exists():
                return False

    # Rule 3: expiry check (skip for 'never')
    if auto_expires != "never" and evidence_path is not None and evidence_path.exists():
        expires_seconds = _parse_duration_to_seconds(auto_expires)
        if expires_seconds is not None:
            commit_ts = _get_evidence_file_commit_time(evidence_path)
            if commit_ts is None:
                # File not committed — fall back to mtime as worst-case
                commit_ts = evidence_path.stat().st_mtime
            elapsed = time.time() - commit_ts
            if elapsed > expires_seconds:
                return False

    # Rule 4: replay protection — (override_id, evidence_file_path) counts once per 30d
    pair_key = (override_id, str(evidence_path) if evidence_path else None)
    thirty_days = 30 * 86400
    now = time.time()
    if pair_key in _SEEN_PAIRS:
        first_seen = _SEEN_PAIRS[pair_key]
        if (now - first_seen) < thirty_days:
            # Already counted — allow but do not double-count
            pass
        else:
            # Older than 30d window — reset
            _SEEN_PAIRS[pair_key] = now
    else:
        _SEEN_PAIRS[pair_key] = now

    return True


# ---------------------------------------------------------------------------
# Override detection
# ---------------------------------------------------------------------------


def _detect_override(
    spec: dict[str, Any],
    payload: dict[str, Any],
    overrides_catalog: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Detect whether a structured override is present for this invocation.

    Looks for STRUCTURED_OVERRIDE env var containing a valid override_id
    that is listed in the hook spec's bypass_policy.override_ids.
    """
    bypass_policy = spec.get("bypass_policy", {})
    if bypass_policy.get("class") == "not_required":
        return None
    allowed_ids: list[str] = bypass_policy.get("override_ids", [])
    requested_id = os.environ.get("STRUCTURED_OVERRIDE", "").strip()
    if not requested_id or requested_id not in allowed_ids:
        return None
    # Find override definition in catalog
    for ov in overrides_catalog:
        if ov["id"] == requested_id:
            return ov
    return None


# ---------------------------------------------------------------------------
# JSON output helpers
# ---------------------------------------------------------------------------


def _emit_deny(reason: str, spec: dict[str, Any]) -> int:
    """PreToolUse → permissionDecision:deny JSON envelope. Other events → exit 2."""
    if spec.get("event") == "PreToolUse":
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
        return 0  # JSON envelope delivered; exit 0 per Claude Code contract
    print(reason, file=sys.stderr)
    return 2


def _emit_advisory(hook_id: str, event: str, additional_context: str) -> int:
    """Emit hookSpecificOutput.additionalContext (no permissionDecision)."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "additionalContext": additional_context,
                }
            }
        )
    )
    return 0


# ---------------------------------------------------------------------------
# Per-hook check stubs (Phase 1: pass-through; Phase 2: real logic)
# ---------------------------------------------------------------------------


def _run_blocking_check(
    spec: dict[str, Any], payload: dict[str, Any]
) -> tuple[str, str]:
    """
    Return (decision, reason) for BLOCKING hooks.
    Phase 1: always allow (parallel-install; hooks not wired to settings.json yet).
    Phase 2 will replace with real check logic per hook_id.
    """
    return "allow", "phase1_stub"


def _run_advisory_check(
    spec: dict[str, Any], payload: dict[str, Any]
) -> str | None:
    """
    Return additionalContext string for ADVISORY hooks, or None.
    Phase 1: return None (no advisory text yet).
    Phase 2 will replace with real logic per hook_id.
    """
    return None


def _log_override_use(
    hook_id: str, override: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Append one line to the override's audit_log jsonl."""
    audit_log_rel: str | None = override.get("audit_log")
    if not audit_log_rel:
        return
    audit_path = REPO_ROOT / audit_log_rel
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "hook_id": hook_id,
        "override_id": override["id"],
        "session_id": payload.get("session_id"),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with audit_path.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------


def main(hook_id: str) -> int:
    try:
        registry = _load_registry()
    except Exception as exc:
        print(f"dispatch.py: failed to load registry: {exc}", file=sys.stderr)
        # Cannot determine severity without registry; fail-closed is safest
        return 2

    try:
        overrides_catalog = _load_overrides().get("overrides", [])
    except Exception:
        overrides_catalog = []

    # Read payload from stdin
    try:
        raw = sys.stdin.read()
        payload: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    event: str = payload.get("hook_event_name", "")

    # Find hook spec
    spec: dict[str, Any] | None = next(
        (h for h in registry.get("hooks", []) if h["id"] == hook_id), None
    )
    if spec is None:
        _emit_signal(hook_id, event, "allow", "missing_spec", payload)
        return 0

    severity: str = spec.get("severity", "ADVISORY")

    try:
        if severity == "ADVISORY":
            ctx = _run_advisory_check(spec, payload)
            _emit_signal(hook_id, event, "allow", "advisory_check", payload)
            if ctx:
                return _emit_advisory(hook_id, event, ctx)
            return 0

        # BLOCKING path
        decision, reason = _run_blocking_check(spec, payload)

        if decision == "deny":
            override = _detect_override(spec, payload, overrides_catalog)
            if override:
                if validate_override(override, spec, payload):
                    _log_override_use(hook_id, override, payload)
                    _emit_signal(
                        hook_id,
                        event,
                        "allow",
                        "override_accepted",
                        payload,
                        override_id=override["id"],
                    )
                    return 0
                _emit_signal(
                    hook_id,
                    event,
                    "deny",
                    "override_invalid",
                    payload,
                    override_id=override["id"],
                )
                return _emit_deny("override evidence invalid", spec)
            _emit_signal(hook_id, event, "deny", reason, payload)
            return _emit_deny(reason, spec)

        _emit_signal(hook_id, event, "allow", "passed", payload)
        return 0

    except Exception as exc:
        # Crash semantics per OD-HOOK-1 / ATTACK 8:
        # ADVISORY → fail-open (exit 0)
        # BLOCKING → fail-closed (exit 2 + stderr)
        _emit_signal(hook_id, event, "error", f"dispatch_crash: {exc}", payload)
        if severity == "ADVISORY":
            return 0
        msg = (
            f"dispatch.py crash on `{hook_id}` — operator must --no-verify to proceed"
        )
        print(msg, file=sys.stderr)
        return 2


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: dispatch.py <hook_id>", file=sys.stderr)
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
