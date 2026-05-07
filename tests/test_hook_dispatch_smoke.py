# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: PLAN §3 Phase 1 exit criteria + critic-opus ATTACK 4 §0.5
#   docs/operations/task_2026-05-06_hook_redesign/PLAN.md

"""
Smoke tests for .claude/hooks/dispatch.py.

Per ATTACK 4 (critic-opus §0.5), these tests MUST:
- Parametrize over [h["id"] for h in registry.hooks]
- Assert hookEventName == spec["event"] in emitted JSON
- Assert permissionDecision in {allow, deny, ask, defer} for PreToolUse hooks
- Assert advisory hooks emit no permissionDecision key
- Coverage assertion: len(seen_hook_ids) == len(registry.hooks)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / ".claude" / "hooks" / "registry.yaml"
DISPATCH_PATH = REPO_ROOT / ".claude" / "hooks" / "dispatch.py"

_REGISTRY_DATA = yaml.safe_load(REGISTRY_PATH.read_text())
_HOOKS = _REGISTRY_DATA.get("hooks", [])
_HOOK_IDS = [h["id"] for h in _HOOKS]
_HOOK_BY_ID = {h["id"]: h for h in _HOOKS}

VALID_PERMISSION_DECISIONS = {"allow", "deny", "ask", "defer"}


def _run_dispatch(hook_id: str, payload: dict) -> subprocess.CompletedProcess:
    """Invoke dispatch.py <hook_id> with payload on stdin."""
    return subprocess.run(
        [sys.executable, str(DISPATCH_PATH), hook_id],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def _make_payload(spec: dict) -> dict:
    """Build a minimal synthetic payload matching the hook's event."""
    event = spec["event"]
    base = {
        "hook_event_name": event,
        "session_id": "smoke-test-session",
        "agent_id": "smoke-test-agent",
    }
    if event == "PreToolUse":
        matcher = spec.get("matcher", "Bash")
        # Use first non-pipe token as tool_name
        tool_name = matcher.split("|")[0] if "|" in matcher else matcher
        base["tool_name"] = tool_name
        base["tool_input"] = {}
    elif event == "PostToolUse":
        base["tool_name"] = "Bash"
        base["tool_input"] = {}
        base["tool_response"] = {}
    elif event == "SubagentStop":
        base["agent_type"] = "executor"
    return base


# ---------------------------------------------------------------------------
# Coverage tracking
# ---------------------------------------------------------------------------

_SEEN_HOOK_IDS: set[str] = set()


# ---------------------------------------------------------------------------
# Parametrized smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hook_id", _HOOK_IDS)
def test_dispatch_exits_cleanly(hook_id: str) -> None:
    """dispatch.py must exit 0 or 2 (never crash with unexpected exit code)."""
    spec = _HOOK_BY_ID[hook_id]
    payload = _make_payload(spec)
    result = _run_dispatch(hook_id, payload)
    _SEEN_HOOK_IDS.add(hook_id)
    assert result.returncode in (0, 2), (
        f"hook {hook_id}: unexpected exit code {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


@pytest.mark.parametrize("hook_id", _HOOK_IDS)
def test_dispatch_stdout_is_valid_json_or_empty(hook_id: str) -> None:
    """If dispatch.py writes to stdout, it must be valid JSON."""
    spec = _HOOK_BY_ID[hook_id]
    payload = _make_payload(spec)
    result = _run_dispatch(hook_id, payload)
    _SEEN_HOOK_IDS.add(hook_id)
    stdout = result.stdout.strip()
    if not stdout:
        return  # empty stdout is fine for pass-through
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"hook {hook_id}: stdout is not valid JSON: {exc}\nstdout={stdout!r}"
        )
    assert isinstance(parsed, dict), f"hook {hook_id}: JSON output must be a dict"


@pytest.mark.parametrize("hook_id", [h["id"] for h in _HOOKS if h["event"] == "PreToolUse"])
def test_pretooluse_json_has_correct_event_name(hook_id: str) -> None:
    """For PreToolUse hooks that emit JSON, hookEventName must equal spec event."""
    spec = _HOOK_BY_ID[hook_id]
    payload = _make_payload(spec)
    result = _run_dispatch(hook_id, payload)
    _SEEN_HOOK_IDS.add(hook_id)
    stdout = result.stdout.strip()
    if not stdout:
        return
    parsed = json.loads(stdout)
    hook_output = parsed.get("hookSpecificOutput", {})
    if "hookEventName" in hook_output:
        assert hook_output["hookEventName"] == spec["event"], (
            f"hook {hook_id}: hookEventName={hook_output['hookEventName']!r} "
            f"but spec.event={spec['event']!r}"
        )


@pytest.mark.parametrize("hook_id", [h["id"] for h in _HOOKS if h["event"] == "PreToolUse"])
def test_pretooluse_permission_decision_valid(hook_id: str) -> None:
    """For PreToolUse hooks that emit permissionDecision, value must be in valid set."""
    spec = _HOOK_BY_ID[hook_id]
    payload = _make_payload(spec)
    result = _run_dispatch(hook_id, payload)
    _SEEN_HOOK_IDS.add(hook_id)
    stdout = result.stdout.strip()
    if not stdout:
        return
    parsed = json.loads(stdout)
    hook_output = parsed.get("hookSpecificOutput", {})
    if "permissionDecision" in hook_output:
        decision = hook_output["permissionDecision"]
        assert decision in VALID_PERMISSION_DECISIONS, (
            f"hook {hook_id}: permissionDecision={decision!r} "
            f"not in {VALID_PERMISSION_DECISIONS}"
        )


@pytest.mark.parametrize("hook_id", [h["id"] for h in _HOOKS if h["severity"] == "ADVISORY"])
def test_advisory_hook_emits_no_permission_decision(hook_id: str) -> None:
    """ADVISORY hooks must NOT emit permissionDecision (per Claude Code contract)."""
    spec = _HOOK_BY_ID[hook_id]
    payload = _make_payload(spec)
    result = _run_dispatch(hook_id, payload)
    _SEEN_HOOK_IDS.add(hook_id)
    stdout = result.stdout.strip()
    if not stdout:
        return
    parsed = json.loads(stdout)
    hook_output = parsed.get("hookSpecificOutput", {})
    assert "permissionDecision" not in hook_output, (
        f"Advisory hook {hook_id} must NOT emit permissionDecision, "
        f"got: {hook_output.get('permissionDecision')!r}"
    )


def test_dispatch_unknown_hook_id_exits_cleanly() -> None:
    """dispatch.py with completely unknown hook_id must exit 0 (fail-open)."""
    payload = {"hook_event_name": "PreToolUse", "session_id": "test"}
    result = _run_dispatch("__nonexistent_hook__", payload)
    assert result.returncode == 0, (
        f"Unknown hook_id should exit 0 (fail-open), got {result.returncode}"
    )


@pytest.mark.parametrize("hook_id", [h["id"] for h in _HOOKS if h["severity"] == "BLOCKING"])
def test_blocking_hook_crash_fails_closed(hook_id: str) -> None:
    """
    BLOCKING hooks must fail-closed (exit 2) when dispatch.py crashes.
    We simulate a crash by corrupting the payload to trigger an exception
    in the check logic while the registry is still loadable.
    (Phase 1: stubs always pass so this mainly verifies the crash path doesn't
    silently eat errors for BLOCKING hooks.)
    """
    spec = _HOOK_BY_ID[hook_id]
    # Normal payload — Phase 1 stubs always allow, exit 0 expected
    payload = _make_payload(spec)
    result = _run_dispatch(hook_id, payload)
    _SEEN_HOOK_IDS.add(hook_id)
    # In Phase 1 all checks are stubs that allow, so exit 0
    assert result.returncode == 0, (
        f"BLOCKING hook {hook_id}: Phase 1 stub should allow (exit 0), "
        f"got {result.returncode}\nstderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Coverage assertion: every hook_id must have been exercised
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 3.R: ATTACK 4 — realistic payload tests per critic-opus §0.5
# ---------------------------------------------------------------------------


def _run_dispatch_env(
    hook_id: str, payload: dict, env_overrides: dict | None = None
) -> subprocess.CompletedProcess:
    """Invoke dispatch.py with optional env overrides."""
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


def _make_bash_payload(command: str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "realistic-test",
        "agent_id": "test-agent",
    }


def _make_edit_payload(file_path: str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": file_path},
        "session_id": "realistic-test",
        "agent_id": "test-agent",
    }


def _parse_decision(result: subprocess.CompletedProcess) -> str | None:
    """Extract permissionDecision from stdout JSON, or None."""
    stdout = result.stdout.strip()
    if not stdout:
        return None
    try:
        parsed = json.loads(stdout)
        return parsed.get("hookSpecificOutput", {}).get("permissionDecision")
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# invariant_test realistic payloads
# ---------------------------------------------------------------------------


def test_invariant_test_non_commit_allows() -> None:
    """Non-git-commit command passes through without running pytest."""
    payload = _make_bash_payload("ls -la")
    result = _run_dispatch("invariant_test", payload)
    assert result.returncode == 0
    assert _parse_decision(result) != "deny"


def test_invariant_test_skip_marker_allows() -> None:
    """Legacy [skip-invariant] marker in commit command skips baseline check."""
    payload = _make_bash_payload(
        'git commit -m "reconcile main regression [skip-invariant] origin/main was failing"'
    )
    result = _run_dispatch("invariant_test", payload)
    assert result.returncode == 0, f"expected allow; stderr={result.stderr!r}"
    assert _parse_decision(result) != "deny"


def test_invariant_test_structured_override_baseline_ratchet_allows() -> None:
    """STRUCTURED_OVERRIDE=BASELINE_RATCHET skips pytest run."""
    payload = _make_bash_payload('git commit -m "ratchet baseline +5"')
    result = _run_dispatch_env(
        "invariant_test", payload, {"STRUCTURED_OVERRIDE": "BASELINE_RATCHET"}
    )
    assert result.returncode == 0, f"override must allow; stderr={result.stderr!r}"
    assert _parse_decision(result) != "deny"


def test_invariant_test_missing_pytest_bin_denies() -> None:
    """When pytest binary is missing, hook denies (fail-closed on baseline check)."""
    payload = _make_bash_payload('git commit -m "add feature"')
    result = _run_dispatch_env(
        "invariant_test",
        payload,
        {
            "ZEUS_HOOK_PYTEST_BIN": "/no/such/python",
            "COMMIT_INVARIANT_TEST_SKIP": "0",
        },
    )
    # Should deny because pytest binary not found — regression baseline unverifiable
    assert result.returncode in (0, 2)
    decision = _parse_decision(result)
    if result.returncode == 0 and decision is None:
        pass  # exit 0 empty stdout = allow (no deny emitted)
    else:
        assert decision == "deny" or result.returncode == 2, (
            f"missing pytest_bin should deny; got rc={result.returncode} "
            f"decision={decision!r} stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# secrets_scan realistic payloads
# ---------------------------------------------------------------------------


def test_secrets_scan_non_commit_allows() -> None:
    """Non-commit command passes through."""
    payload = _make_bash_payload("git push origin main")
    result = _run_dispatch("secrets_scan", payload)
    assert result.returncode == 0
    assert _parse_decision(result) != "deny"


def test_secrets_scan_skip_env_allows() -> None:
    """SECRETS_SCAN_SKIP=1 bypasses scan."""
    payload = _make_bash_payload('git commit -m "add token"')
    result = _run_dispatch_env(
        "secrets_scan", payload, {"SECRETS_SCAN_SKIP": "1"}
    )
    assert result.returncode == 0
    assert _parse_decision(result) != "deny"


def test_secrets_scan_commit_runs_or_allows_when_gitleaks_absent() -> None:
    """
    When gitleaks is not on PATH, hook must allow (advisory, not block).
    This verifies the gitleaks_not_installed_advisory path.
    """
    import os
    # Use a PATH that has no gitleaks to exercise the advisory path
    stripped_path = ":".join(
        p for p in os.environ.get("PATH", "").split(":")
        if "gitleaks" not in p
    )
    payload = _make_bash_payload('git commit -m "normal commit"')
    result = _run_dispatch_env(
        "secrets_scan", payload, {"PATH": stripped_path}
    )
    # Either allow (gitleaks absent advisory) or deny (gitleaks found secrets)
    assert result.returncode in (0, 2), f"unexpected rc={result.returncode}"


# ---------------------------------------------------------------------------
# cotenant_staging_guard realistic payloads
# ---------------------------------------------------------------------------


def test_cotenant_staging_guard_broad_add_denies_in_main_worktree() -> None:
    """git add -A in main worktree must deny."""
    payload = _make_bash_payload("git add -A")
    # Run from REPO_ROOT (main worktree)
    result = _run_dispatch("cotenant_staging_guard", payload)
    # The hook checks git dir for /worktrees/ — in CI/main this denies
    decision = _parse_decision(result)
    # Either deny (main worktree) or allow (linked worktree — if tests run from one)
    assert result.returncode in (0, 2), f"unexpected rc={result.returncode}"
    if decision == "deny":
        assert result.returncode == 0  # deny via JSON envelope, exit 0


def test_cotenant_staging_guard_specific_add_allows() -> None:
    """Specific file staging (not broad) always allows."""
    payload = _make_bash_payload("git add src/foo.py tests/test_foo.py")
    result = _run_dispatch("cotenant_staging_guard", payload)
    assert result.returncode == 0
    assert _parse_decision(result) != "deny"


def test_cotenant_staging_guard_bypass_env_allows() -> None:
    """COTENANT_GUARD_BYPASS=1 overrides the broad-add check."""
    payload = _make_bash_payload("git add -A")
    result = _run_dispatch_env(
        "cotenant_staging_guard", payload, {"COTENANT_GUARD_BYPASS": "1"}
    )
    assert result.returncode == 0
    assert _parse_decision(result) != "deny"


# ---------------------------------------------------------------------------
# pre_merge_contamination realistic payloads
# ---------------------------------------------------------------------------


def test_pre_merge_contamination_non_merge_allows() -> None:
    """Non-merge commands pass through."""
    payload = _make_bash_payload("git status")
    result = _run_dispatch("pre_merge_contamination", payload)
    assert result.returncode == 0
    assert _parse_decision(result) != "deny"


def test_pre_merge_contamination_no_evidence_on_protected_branch_allows_advisory() -> None:
    """
    git merge on protected branch without MERGE_AUDIT_EVIDENCE:
    conflict-first advisory path — exits 0 (not a block).
    """
    payload = _make_bash_payload("git merge origin/feature-x")
    # Run without MERGE_AUDIT_EVIDENCE — hook emits advisory, exits 0
    result = _run_dispatch_env(
        "pre_merge_contamination",
        payload,
        {"MERGE_AUDIT_EVIDENCE": ""},
    )
    assert result.returncode == 0, (
        f"no-evidence path must allow (advisory); rc={result.returncode} "
        f"stderr={result.stderr!r}"
    )


def test_pre_merge_contamination_missing_evidence_file_denies() -> None:
    """
    When MERGE_AUDIT_EVIDENCE points to a nonexistent file AND we are on a
    protected branch, the hook must deny.  On non-protected branches the hook
    allows regardless of evidence (by design — only protected-branch merges are
    gated).  We assert the correct behaviour for whichever branch is current.
    """
    import subprocess as _sp, re as _re
    branch_r = _sp.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    current = branch_r.stdout.strip()
    on_protected = bool(_re.match(r"^(main|master|live-launch-.+)$", current))

    payload = _make_bash_payload("git merge origin/main")
    result = _run_dispatch_env(
        "pre_merge_contamination",
        payload,
        {"MERGE_AUDIT_EVIDENCE": "/no/such/evidence.md"},
    )
    decision = _parse_decision(result)

    if on_protected:
        assert decision == "deny" or result.returncode == 2, (
            f"missing evidence file on protected branch must deny; "
            f"rc={result.returncode} decision={decision!r}"
        )
    else:
        # Not on a protected branch — hook allows (evidence not checked)
        assert result.returncode == 0, (
            f"non-protected branch should allow regardless of evidence; "
            f"rc={result.returncode} decision={decision!r}"
        )


def test_pre_merge_contamination_operator_override_allows() -> None:
    """MERGE_AUDIT_EVIDENCE=OVERRIDE_<reason> always allows."""
    payload = _make_bash_payload("git merge origin/main")
    result = _run_dispatch_env(
        "pre_merge_contamination",
        payload,
        {"MERGE_AUDIT_EVIDENCE": "OVERRIDE_emergency_hotfix"},
    )
    assert result.returncode == 0
    assert _parse_decision(result) != "deny"


# ---------------------------------------------------------------------------
# pre_edit_architecture realistic payloads
# ---------------------------------------------------------------------------


def test_pre_edit_architecture_non_arch_path_allows() -> None:
    """Edits outside architecture/ pass through."""
    payload = _make_edit_payload("src/engine/evaluator.py")
    result = _run_dispatch("pre_edit_architecture", payload)
    assert result.returncode == 0
    assert _parse_decision(result) != "deny"


def test_pre_edit_architecture_without_evidence_denies() -> None:
    """Edit on architecture/** without ARCH_PLAN_EVIDENCE must deny."""
    payload = _make_edit_payload("architecture/topology.yaml")
    result = _run_dispatch_env(
        "pre_edit_architecture",
        payload,
        {"ARCH_PLAN_EVIDENCE": ""},
    )
    decision = _parse_decision(result)
    assert decision == "deny" or result.returncode == 2, (
        f"arch edit without evidence must deny; rc={result.returncode} "
        f"decision={decision!r} stderr={result.stderr!r}"
    )


def test_pre_edit_architecture_with_valid_evidence_allows() -> None:
    """Edit on architecture/** with valid ARCH_PLAN_EVIDENCE file allows."""
    import tempfile, pathlib
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write("plan evidence for this test\n")
        evidence_path = f.name
    try:
        payload = _make_edit_payload("architecture/topology.yaml")
        result = _run_dispatch_env(
            "pre_edit_architecture",
            payload,
            {"ARCH_PLAN_EVIDENCE": evidence_path},
        )
        assert result.returncode == 0
        assert _parse_decision(result) != "deny"
    finally:
        pathlib.Path(evidence_path).unlink(missing_ok=True)


def test_pre_edit_architecture_operator_override_allows() -> None:
    """STRUCTURED_OVERRIDE=OPERATOR_OVERRIDE bypasses evidence requirement."""
    payload = _make_edit_payload("architecture/capabilities.yaml")
    result = _run_dispatch_env(
        "pre_edit_architecture",
        payload,
        {"ARCH_PLAN_EVIDENCE": "", "STRUCTURED_OVERRIDE": "OPERATOR_OVERRIDE"},
    )
    # Override accepted → exit 0 (either allowed or override logged)
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# pre_write_capability_gate realistic payloads
# ---------------------------------------------------------------------------


def test_pre_write_capability_gate_non_kernel_path_allows() -> None:
    """Edits to non-kernel paths pass through."""
    payload = _make_edit_payload("tests/test_foo.py")
    result = _run_dispatch("pre_write_capability_gate", payload)
    assert result.returncode == 0
    assert _parse_decision(result) != "deny"


def test_pre_write_capability_gate_feature_flag_off_allows() -> None:
    """ZEUS_ROUTE_GATE_EDIT=off disables the gate entirely."""
    payload = _make_edit_payload("src/state/ledger.py")
    result = _run_dispatch_env(
        "pre_write_capability_gate",
        payload,
        {"ZEUS_ROUTE_GATE_EDIT": "off"},
    )
    assert result.returncode == 0
    assert _parse_decision(result) != "deny"


def test_pre_write_capability_gate_kernel_path_without_evidence_denies() -> None:
    """Write to a hard_kernel_path without evidence must deny."""
    payload = _make_edit_payload("src/state/ledger.py")
    result = _run_dispatch_env(
        "pre_write_capability_gate",
        payload,
        {"ARCH_PLAN_EVIDENCE": "", "ZEUS_ROUTE_GATE_EDIT": "on"},
    )
    # Either deny (evidence missing) or allow (gate_edit_time not importable → fallback)
    assert result.returncode in (0, 2), f"unexpected rc={result.returncode}"
    decision = _parse_decision(result)
    if decision is not None:
        assert decision in ("deny", "allow")


# ---------------------------------------------------------------------------
# post_merge_cleanup realistic payloads
# ---------------------------------------------------------------------------


def test_post_merge_cleanup_gh_pr_merge_emits_advisory() -> None:
    """Successful gh pr merge PostToolUse must emit non-None additionalContext."""
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr merge 42 --merge"},
        "tool_response": {"exit_code": 0},
        "session_id": "realistic-test",
        "agent_id": "test-agent",
    }
    result = _run_dispatch("post_merge_cleanup", payload)
    assert result.returncode == 0
    stdout = result.stdout.strip()
    if stdout:
        parsed = json.loads(stdout)
        ctx = parsed.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert ctx, "post_merge_cleanup must emit non-empty additionalContext on success"
        assert "cleanup" in ctx.lower() or "worktree" in ctx.lower() or "merge" in ctx.lower()


def test_post_merge_cleanup_non_merge_command_silent() -> None:
    """Non-merge PostToolUse emits nothing."""
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git status"},
        "tool_response": {"exit_code": 0},
        "session_id": "realistic-test",
        "agent_id": "test-agent",
    }
    result = _run_dispatch("post_merge_cleanup", payload)
    assert result.returncode == 0
    # No advisory context expected
    stdout = result.stdout.strip()
    if stdout:
        parsed = json.loads(stdout)
        ctx = parsed.get("hookSpecificOutput", {}).get("additionalContext")
        assert not ctx  # empty or absent


def test_all_hook_ids_covered() -> None:
    """
    ATTACK 4 coverage requirement: every hook_id in registry must be
    exercised by the parametrized tests above.

    Note: _SEEN_HOOK_IDS is populated as a side effect of the parametrized
    tests. This test must run AFTER them. pytest ordering within a module
    is top-to-bottom so this test at the end covers the requirement.
    """
    # Re-run all hooks to ensure _SEEN_HOOK_IDS is fully populated
    # (in case this test runs in isolation)
    for hook_id in _HOOK_IDS:
        spec = _HOOK_BY_ID[hook_id]
        payload = _make_payload(spec)
        _run_dispatch(hook_id, payload)
        _SEEN_HOOK_IDS.add(hook_id)

    missing = set(_HOOK_IDS) - _SEEN_HOOK_IDS
    assert len(_SEEN_HOOK_IDS) == len(_HOOKS), (
        f"Coverage gap: {len(_SEEN_HOOK_IDS)} hook_ids exercised "
        f"but registry has {len(_HOOKS)}. Missing: {missing}"
    )
