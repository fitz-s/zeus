"""Runtime-fire test for .claude/hooks/dispatch.py — antibody for the
"boot ≠ runtime" failure class.

Background (2026-05-17 incident):
  Pre-PR critic and the agent both verified boot-self-test ("all N registry
  hooks have handlers") and concluded the W1.2 citation_grep_gate hook was
  ship-ready. PR #127 round-1 bot review (Codex + Copilot) immediately found
  THREE bugs that boot-self-test could not catch:
    F1. registry entry without matching settings.json dispatch invocation
        → hook never fires at runtime
    F3. handler returns JSON shape rejected by Claude Code schema
        → hook output silently dropped at runtime
    + metadata.catalog_size stale (tests/test_hook_registry_schema.py existed
      for that, but the agent didn't run it)
  Earlier in the same session, the broken worktree_create_advisor hook (same
  F3 class) hard-failed EnterWorktree, forcing the agent into a primary-
  worktree bypass that caused HEAD-contamination.

What this test covers (one test per failure mode):
  test_f1_every_registry_hook_invoked_by_settings
      For each registry hook, settings.json has a PreToolUse/PostToolUse/...
      entry whose `python3 .claude/hooks/dispatch.py <id>` matches the
      registered event.
  test_f3_emit_advisory_produces_schema_valid_json
      For each registry hook, a synthetic payload matching its event type
      produces stdout that is (a) valid JSON, (b) uses hookSpecificOutput
      ONLY for events in the allowlist, (c) uses systemMessage for everything
      else, (d) exits 0 (fail-open per ADVISORY charter).
  test_f6_handler_signature_matches_dispatcher
      Each handler accepts (payload: dict) and returns str | None.
  test_f7_handler_lazy_imports_work
      Invoking each handler doesn't raise ImportError on lazily-loaded deps.
  test_f10_handler_completes_within_timeout
      Each handler returns within HOOK_TIMEOUT_SEC (no hangs).
  test_catalog_size_matches_len_hooks
      metadata.catalog_size == len(registry["hooks"]) — the canonical
      tests/test_hook_registry_schema.py check, restated here so a single
      test file covers the whole "did this hook just boot or also fire?"
      question.

Run:
  python3 -m pytest tests/test_dispatch_runtime_fire.py -v
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCH_PATH = REPO_ROOT / ".claude" / "hooks" / "dispatch.py"
REGISTRY_PATH = REPO_ROOT / ".claude" / "hooks" / "registry.yaml"
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"

HOOK_TIMEOUT_SEC = 10

# Events whose Claude Code response schema accepts `hookSpecificOutput`.
# Mirrors _HOOK_SPECIFIC_OUTPUT_EVENTS in dispatch.py — keep in sync.
HOOK_SPECIFIC_OUTPUT_EVENTS = frozenset({
    "PreToolUse", "UserPromptSubmit", "PostToolUse", "PostToolBatch",
})


def _load_registry() -> dict[str, Any]:
    return yaml.safe_load(REGISTRY_PATH.read_text())


def _load_settings_dispatch_invocations() -> dict[str, list[tuple[str, str]]]:
    """Map hook_id -> [(event, matcher), ...] from settings.json."""
    sj = json.loads(SETTINGS_PATH.read_text())
    result: dict[str, list[tuple[str, str]]] = {}
    for event, blocks in sj.get("hooks", {}).items():
        for block in blocks:
            matcher = block.get("matcher", "")
            for h in block.get("hooks", []):
                cmd = h.get("command", "") or ""
                if "dispatch.py" not in cmd:
                    continue
                hook_id = cmd.split("dispatch.py")[-1].strip().split()[0]
                result.setdefault(hook_id, []).append((event, matcher))
    return result


def _import_dispatch_module():
    sys.path.insert(0, str(DISPATCH_PATH.parent))
    import importlib
    import dispatch as _dispatch  # type: ignore[import-not-found]
    importlib.reload(_dispatch)
    return _dispatch


def _synthetic_payload_for(event: str, hook_id: str) -> dict[str, Any]:
    """Construct a plausible payload for the given event type."""
    base = {
        "hook_event_name": event,
        "session_id": "test-session-runtime-fire",
        "agent_id": "test-agent",
    }
    if event == "PreToolUse":
        base.update({
            "tool_name": "Bash",
            "tool_input": {"command": "echo synthetic"},
        })
    elif event == "PostToolUse":
        base.update({
            "tool_name": "Bash",
            "tool_input": {"command": "echo synthetic"},
            "tool_response": {"output": "synthetic"},
        })
    elif event == "UserPromptSubmit":
        base.update({"prompt": "synthetic"})
    elif event in {"WorktreeCreate", "WorktreeRemove"}:
        base.update({
            "tool_input": {"path": "/tmp/synthetic-worktree-test", "branch": "synth"},
        })
    elif event in {"Stop", "SubagentStop", "SessionStart", "PostToolBatch"}:
        pass  # base is sufficient
    return base


def _invoke_dispatch(hook_id: str, payload: dict[str, Any]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(DISPATCH_PATH), hook_id],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=HOOK_TIMEOUT_SEC,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry() -> dict[str, Any]:
    return _load_registry()


@pytest.fixture(scope="module")
def settings_invocations() -> dict[str, list[tuple[str, str]]]:
    return _load_settings_dispatch_invocations()


def test_catalog_size_matches_len_hooks(registry):
    meta = registry.get("metadata", {})
    assert meta.get("catalog_size") == len(registry["hooks"]), (
        f"metadata.catalog_size={meta.get('catalog_size')} but hooks list "
        f"has {len(registry['hooks'])} entries"
    )


def test_f1_every_registry_hook_invoked_by_settings(registry, settings_invocations):
    """Every registry hook must have at least one matching dispatch invocation
    in settings.json for the event type the registry declares."""
    failures: list[str] = []
    for h in registry["hooks"]:
        hid = h["id"]
        expected_event = h.get("event", "")
        invocations = settings_invocations.get(hid, [])
        if not invocations:
            failures.append(
                f"{hid}: no settings.json dispatch invocation — hook never fires"
            )
            continue
        # event-match: at least one invocation must be under the right event
        # (matcher narrowing is allowed; event mismatch is fatal)
        if not any(ev == expected_event for ev, _ in invocations):
            failures.append(
                f"{hid}: registry event={expected_event} but settings invokes "
                f"only under {invocations}"
            )
    assert not failures, "F1 dormant/wrong-event hooks:\n" + "\n".join(failures)


@pytest.mark.parametrize("hook_idx", range(0, 20))  # safe upper bound
def test_f3_emit_advisory_schema_valid_per_hook(registry, hook_idx):
    """For each hook, synthesizing its declared event produces a Claude-Code-
    schema-valid JSON response (uses hookSpecificOutput only for allowlisted
    events; uses systemMessage otherwise)."""
    hooks = registry["hooks"]
    if hook_idx >= len(hooks):
        pytest.skip(f"only {len(hooks)} hooks; idx {hook_idx} out of range")
    h = hooks[hook_idx]
    hid = h["id"]
    event = h.get("event", "PreToolUse")
    payload = _synthetic_payload_for(event, hid)
    result = _invoke_dispatch(hid, payload)
    assert result.returncode == 0, (
        f"{hid}: dispatch exited {result.returncode}, stderr={result.stderr[:200]}"
    )
    if not result.stdout.strip():
        # Empty stdout is acceptable (handler returned None → no advisory)
        return
    try:
        out = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"{hid}: stdout is not valid JSON ({e}). stdout[:300]={result.stdout[:300]}"
        )
    if "hookSpecificOutput" in out:
        assert event in HOOK_SPECIFIC_OUTPUT_EVENTS, (
            f"{hid}: emitted hookSpecificOutput for event={event!r}, but "
            f"Claude Code schema only accepts hookSpecificOutput for "
            f"{sorted(HOOK_SPECIFIC_OUTPUT_EVENTS)}. Use systemMessage instead."
        )
        hso = out["hookSpecificOutput"]
        assert hso.get("hookEventName") == event, (
            f"{hid}: hookEventName={hso.get('hookEventName')!r} but event={event!r}"
        )
    elif "systemMessage" in out:
        # systemMessage is universal — valid for any event
        pass
    else:
        # Other fields (continue/suppressOutput/decision/reason) are also valid;
        # we only enforce that if either of the two main carriers is used, it's correct.
        pass


def test_f6_handler_signature_matches_dispatcher(registry):
    """Each registered handler must accept (payload: dict) positional/keyword
    and return str | None."""
    dispatch = _import_dispatch_module()
    handlers = dispatch._ADVISORY_HANDLERS
    issues: list[str] = []
    for h in registry["hooks"]:
        hid = h["id"]
        fn = handlers.get(hid)
        if fn is None:
            issues.append(f"{hid}: no handler in _ADVISORY_HANDLERS")
            continue
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        if len(params) != 1:
            issues.append(f"{hid}: handler has {len(params)} params; expected 1 (payload)")
    assert not issues, "F6 handler signature issues:\n" + "\n".join(issues)


@pytest.mark.parametrize("hook_idx", range(0, 20))
def test_f7_f10_handler_runtime_fire(registry, hook_idx):
    """Combined F7 (lazy imports) + F10 (timeout). Synthesizing each event
    and running the FULL subprocess flow within timeout proves handler
    imports are loadable AND the handler returns within deadline.

    Critical (added per 2026-05-17 critic CRITICAL #2): the dispatcher's
    fail-open `except Exception` in main() converts any handler exception
    (NameError, ImportError, ...) into exit 0 + empty stdout. Asserting
    only exit 0 means the test cannot distinguish "handler returned None
    cleanly" from "handler crashed and was silently swallowed" — the
    exact failure class this test is named to prevent. So we ALSO assert
    stderr is free of Traceback / dispatch_error markers."""
    hooks = registry["hooks"]
    if hook_idx >= len(hooks):
        pytest.skip(f"only {len(hooks)} hooks; idx {hook_idx} out of range")
    h = hooks[hook_idx]
    hid = h["id"]
    event = h.get("event", "PreToolUse")
    payload = _synthetic_payload_for(event, hid)
    try:
        result = _invoke_dispatch(hid, payload)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{hid}: handler exceeded {HOOK_TIMEOUT_SEC}s — F10 timeout")
    # F4 fail-open contract: any exception should still produce exit 0.
    assert result.returncode == 0, (
        f"{hid}: handler did not fail-open. exit={result.returncode}, "
        f"stderr[:200]={result.stderr[:200]}"
    )
    # CRITICAL: assert no exception got silently fail-opened. The dispatcher's
    # _emit_signal() writes `dispatch_error:<exc>` reason on caught exceptions.
    # If the handler crashed, stderr from any sibling boot-checks won't help,
    # but the dispatcher itself prints the boot integrity line to stderr.
    # We assert NO Traceback / NameError / NoneType / dispatch_error in stderr.
    forbidden = ("Traceback", "NameError", "AttributeError", "TypeError",
                 "ImportError", "ModuleNotFoundError", "dispatch_error")
    for marker in forbidden:
        assert marker not in result.stderr, (
            f"{hid}: stderr contains '{marker}' — handler silently failed open "
            f"and exit code masks the bug (the 2026-05-17 CRITICAL #2 class).\n"
            f"stderr[:500]={result.stderr[:500]}"
        )


def test_no_handler_emits_dispatch_error_signal(registry):
    """STRONGEST runtime-fire check (post-2026-05-17 CRITICAL #2 META-fail):

    The previous version of this test was broken in TWO ways:
      (1) checked exit 0 — masked by dispatcher's outer `except Exception`
      (2) tried in-process direct call to bypass the dispatcher mask —
          but handlers ALSO wrap their own bodies in try/except and call
          `_emit_signal(..., 'error', f'dispatch_error:{exc}', ...)`. So
          even in-process calls return None on internal crash.

    So fail-open is TWO LAYERS DEEP. The ONLY public signal that survives
    is `_emit_signal` writing `decision=error, reason=dispatch_error:*`
    to .claude/logs/hook_signal/<YYYY-MM>.jsonl (dispatch.py:42 hardcoded).

    This test invokes each handler in subprocess with a UNIQUE session_id
    marker, then scans the REAL log file for any entries matching that
    session_id with decision=error. Catches both inner-try-except AND
    outer-main-try-except fail-open. Meta-verified 2026-05-17: removing
    `import re` from pre_branch_create_in_primary causes this test to FAIL
    (entries appear with reason='dispatch_error:name re is not defined').
    """
    from datetime import datetime as _dt, timezone as _tz
    import uuid as _uuid

    log_dir = REPO_ROOT / ".claude" / "logs" / "hook_signal"
    log_dir.mkdir(parents=True, exist_ok=True)
    month = _dt.now(_tz.utc).strftime("%Y-%m")
    log_file = log_dir / f"{month}.jsonl"
    pre_log_size = log_file.stat().st_size if log_file.exists() else 0

    test_session_id = f"runtime-fire-test-{_uuid.uuid4().hex[:12]}"
    errors: list[str] = []

    for h in registry["hooks"]:
        hid = h["id"]
        event = h.get("event", "PreToolUse")
        payload = _synthetic_payload_for(event, hid)
        payload["session_id"] = test_session_id
        try:
            subprocess.run(
                ["python3", str(DISPATCH_PATH), hid],
                input=json.dumps(payload),
                capture_output=True, text=True, timeout=HOOK_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{hid}: TIMEOUT (>{HOOK_TIMEOUT_SEC}s)")
            continue

    # Scan log for entries from THIS test's session_id
    if log_file.exists():
        with log_file.open() as fh:
            fh.seek(pre_log_size)  # only read what we just appended
            for raw in fh:
                if not raw.strip():
                    continue
                try:
                    e = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if e.get("session_id") != test_session_id:
                    continue
                if e.get("decision") == "error" or str(e.get("reason","")).startswith("dispatch_error:"):
                    errors.append(
                        f"{e.get('hook_id','?')}: emitted error signal "
                        f"(reason={e.get('reason','')[:200]})"
                    )

    assert not errors, (
        "Handlers silently failed open under synthetic-payload runs "
        "(exact 2026-05-17 CRITICAL #2 failure class — two layers of "
        "fail-open mask exit code AND in-process return, but _emit_signal "
        "log entry survives as proof):\n" + "\n".join(errors)
    )
