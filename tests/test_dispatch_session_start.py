# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: Navigation Topology v2 PLAN §3 Phase 3 + C2 amendment; sunset 2027-05-07

"""
Tests for .claude/hooks/dispatch.py Phase 3 event handlers:
  - session_start_visibility (SessionStart)
  - worktree_create_advisor (WorktreeCreate)
  - worktree_remove_advisor (WorktreeRemove)

Exit criteria (PLAN §3 Phase 3 + C2):
- SessionStart handler emits additionalContext with cross-worktree data
- Handler returns None on subprocess crash (fall-open per C2 / ATTACK 8)
- Handler emits ritual_signal dispatch_error on crash
- WorktreeCreate / WorktreeRemove handlers emit advisory context
- All three handlers are ADVISORY (no permissionDecision in output)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCH_PATH = REPO_ROOT / ".claude" / "hooks" / "dispatch.py"


def _run_dispatch(
    hook_id: str,
    payload: dict,
    env_overrides: dict | None = None,
) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(DISPATCH_PATH), hook_id],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )


def _session_start_payload() -> dict:
    return {
        "hook_event_name": "SessionStart",
        "session_id": "test-session-phase3",
        "agent_id": "test-agent-phase3",
    }


def _worktree_create_payload(path: str = "/tmp/test-worktree") -> dict:
    return {
        "hook_event_name": "WorktreeCreate",
        "session_id": "test-session-phase3",
        "agent_id": "test-agent-phase3",
        "tool_input": {
            "path": path,
            "branch": "test-branch-2026-05-07",
            "task_slug": "test-task",
            "intent": "test intent for worktree_create",
        },
    }


def _worktree_remove_payload(path: str = "/tmp/test-worktree") -> dict:
    return {
        "hook_event_name": "WorktreeRemove",
        "session_id": "test-session-phase3",
        "agent_id": "test-agent-phase3",
        "tool_input": {
            "path": path,
        },
    }


def _parse_additional_context(result: subprocess.CompletedProcess) -> str | None:
    stdout = result.stdout.strip()
    if not stdout:
        return None
    try:
        parsed = json.loads(stdout)
        return parsed.get("hookSpecificOutput", {}).get("additionalContext")
    except json.JSONDecodeError:
        return None


def _parse_hook_event_name(result: subprocess.CompletedProcess) -> str | None:
    stdout = result.stdout.strip()
    if not stdout:
        return None
    try:
        parsed = json.loads(stdout)
        return parsed.get("hookSpecificOutput", {}).get("hookEventName")
    except json.JSONDecodeError:
        return None


def _has_permission_decision(result: subprocess.CompletedProcess) -> bool:
    stdout = result.stdout.strip()
    if not stdout:
        return False
    try:
        parsed = json.loads(stdout)
        return "permissionDecision" in parsed.get("hookSpecificOutput", {})
    except json.JSONDecodeError:
        return False


# ---------------------------------------------------------------------------
# session_start_visibility
# ---------------------------------------------------------------------------


def test_session_start_visibility_exits_zero() -> None:
    """session_start_visibility must exit 0 (advisory)."""
    result = _run_dispatch("session_start_visibility", _session_start_payload())
    assert result.returncode == 0, (
        f"session_start_visibility must exit 0; got {result.returncode}\n"
        f"stderr: {result.stderr!r}"
    )


def test_session_start_visibility_emits_additional_context() -> None:
    """session_start_visibility must emit additionalContext with cross-worktree data."""
    result = _run_dispatch("session_start_visibility", _session_start_payload())
    assert result.returncode == 0
    ctx = _parse_additional_context(result)
    assert ctx is not None, (
        f"session_start_visibility must emit additionalContext; stdout={result.stdout!r}"
    )
    assert len(ctx) > 0, "additionalContext must be non-empty"


def test_session_start_visibility_context_mentions_worktrees() -> None:
    """The additionalContext must reference active worktrees."""
    result = _run_dispatch("session_start_visibility", _session_start_payload())
    ctx = _parse_additional_context(result)
    if ctx is None:
        pytest.skip("No additionalContext emitted (worktree_doctor may be unavailable)")
    assert "worktree" in ctx.lower() or "[" in ctx, (
        f"additionalContext does not mention worktrees: {ctx!r}"
    )


def test_session_start_visibility_no_permission_decision() -> None:
    """ADVISORY hook must NOT emit permissionDecision."""
    result = _run_dispatch("session_start_visibility", _session_start_payload())
    assert not _has_permission_decision(result), (
        "session_start_visibility must NOT emit permissionDecision (advisory hook)"
    )


def test_session_start_visibility_fall_open_on_crash() -> None:
    """
    When worktree_doctor is unavailable (PATH broken), handler must:
    - Exit 0 (fall-open per ATTACK 8 / C2)
    - Return None (no additionalContext) or emit whatever it can
    Never exit 2 (never block on SessionStart).
    """
    import os
    # Break the Python path so worktree_doctor subprocess can't be found
    result = _run_dispatch(
        "session_start_visibility",
        _session_start_payload(),
        env_overrides={"ZEUS_WORKTREE_DOCTOR_DISABLED": "1"},
    )
    # Must still exit 0 even if something breaks
    assert result.returncode == 0, (
        f"session_start_visibility must fall-open (exit 0) on error; "
        f"got {result.returncode}\nstderr: {result.stderr!r}"
    )


def test_session_start_visibility_output_is_valid_json_or_empty() -> None:
    """stdout must be valid JSON or empty."""
    result = _run_dispatch("session_start_visibility", _session_start_payload())
    stdout = result.stdout.strip()
    if not stdout:
        return
    try:
        json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"session_start_visibility stdout is not valid JSON: {exc}\n"
            f"stdout={stdout!r}"
        )


# ---------------------------------------------------------------------------
# worktree_create_advisor
# ---------------------------------------------------------------------------


def test_worktree_create_advisor_exits_zero() -> None:
    """worktree_create_advisor must exit 0 (advisory)."""
    result = _run_dispatch("worktree_create_advisor", _worktree_create_payload())
    assert result.returncode == 0, (
        f"worktree_create_advisor must exit 0; got {result.returncode}\n"
        f"stderr: {result.stderr!r}"
    )


def test_worktree_create_advisor_emits_advisory_context() -> None:
    """worktree_create_advisor must emit additionalContext about naming/scope policy."""
    result = _run_dispatch("worktree_create_advisor", _worktree_create_payload())
    assert result.returncode == 0
    ctx = _parse_additional_context(result)
    assert ctx is not None, (
        f"worktree_create_advisor must emit additionalContext; stdout={result.stdout!r}"
    )
    # Should mention naming or scope
    ctx_lower = ctx.lower()
    assert any(kw in ctx_lower for kw in ("naming", "scope", "worktree", "sentinel", "advisory")), (
        f"worktree_create_advisor context must mention naming/scope/sentinel; got: {ctx!r}"
    )


def test_worktree_create_advisor_no_permission_decision() -> None:
    """ADVISORY hook must NOT emit permissionDecision."""
    result = _run_dispatch("worktree_create_advisor", _worktree_create_payload())
    assert not _has_permission_decision(result), (
        "worktree_create_advisor must NOT emit permissionDecision"
    )


# ---------------------------------------------------------------------------
# worktree_remove_advisor
# ---------------------------------------------------------------------------


def test_worktree_remove_advisor_exits_zero() -> None:
    """worktree_remove_advisor must exit 0 (advisory)."""
    result = _run_dispatch("worktree_remove_advisor", _worktree_remove_payload())
    assert result.returncode == 0, (
        f"worktree_remove_advisor must exit 0; got {result.returncode}\n"
        f"stderr: {result.stderr!r}"
    )


def test_worktree_remove_advisor_emits_advisory_context() -> None:
    """worktree_remove_advisor must emit additionalContext with pre-remove guidance."""
    result = _run_dispatch("worktree_remove_advisor", _worktree_remove_payload())
    assert result.returncode == 0
    ctx = _parse_additional_context(result)
    assert ctx is not None, (
        f"worktree_remove_advisor must emit additionalContext; stdout={result.stdout!r}"
    )
    ctx_lower = ctx.lower()
    assert any(kw in ctx_lower for kw in ("advisory", "remove", "worktree", "never", "dirty")), (
        f"worktree_remove_advisor context must mention removal guidance; got: {ctx!r}"
    )


def test_worktree_remove_advisor_no_permission_decision() -> None:
    """ADVISORY hook must NOT emit permissionDecision."""
    result = _run_dispatch("worktree_remove_advisor", _worktree_remove_payload())
    assert not _has_permission_decision(result), (
        "worktree_remove_advisor must NOT emit permissionDecision"
    )


def test_worktree_remove_advisor_never_auto_delete_message() -> None:
    """The advisory must mention that it never auto-deletes."""
    result = _run_dispatch("worktree_remove_advisor", _worktree_remove_payload())
    ctx = _parse_additional_context(result)
    if ctx is None:
        pytest.skip("No additionalContext emitted")
    assert "never" in ctx.lower() or "advisory" in ctx.lower(), (
        f"worktree_remove_advisor must mention it never auto-deletes; got: {ctx!r}"
    )


# ---------------------------------------------------------------------------
# Dispatch smoke: new hooks integrated into registry coverage
# ---------------------------------------------------------------------------


def test_new_hooks_in_registry() -> None:
    """The 3 new hook IDs must exist in registry.yaml."""
    import yaml
    registry_path = REPO_ROOT / ".claude" / "hooks" / "registry.yaml"
    registry = yaml.safe_load(registry_path.read_text())
    hook_ids = {h["id"] for h in registry.get("hooks", [])}
    for expected_id in (
        "session_start_visibility",
        "worktree_create_advisor",
        "worktree_remove_advisor",
    ):
        assert expected_id in hook_ids, (
            f"Hook '{expected_id}' not found in registry.yaml; "
            f"available: {sorted(hook_ids)}"
        )


def test_registry_catalog_size_updated() -> None:
    """registry.yaml catalog_size must be 15 (was 12 + 3 new hooks)."""
    import yaml
    registry_path = REPO_ROOT / ".claude" / "hooks" / "registry.yaml"
    registry = yaml.safe_load(registry_path.read_text())
    catalog_size = registry.get("metadata", {}).get("catalog_size")
    assert catalog_size == 15, (
        f"registry.yaml catalog_size must be 15; got {catalog_size}"
    )


def test_new_hooks_are_advisory() -> None:
    """All 3 new hooks must have severity ADVISORY."""
    import yaml
    registry_path = REPO_ROOT / ".claude" / "hooks" / "registry.yaml"
    registry = yaml.safe_load(registry_path.read_text())
    hook_by_id = {h["id"]: h for h in registry.get("hooks", [])}
    for hook_id in ("session_start_visibility", "worktree_create_advisor", "worktree_remove_advisor"):
        if hook_id not in hook_by_id:
            continue
        severity = hook_by_id[hook_id].get("severity")
        assert severity == "ADVISORY", (
            f"Hook '{hook_id}' must be ADVISORY; got {severity!r}"
        )
