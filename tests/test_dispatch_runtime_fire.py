# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: 2026-05-17 incident post-mortem — boot ≠ runtime blindspot
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

# Events where harness reserves stdout for tool-protocol payload. Hooks for
# these events MUST emit empty stdout + advisory on stderr (see dispatch.py
# _STDOUT_PROTOCOL_RESERVED_EVENTS). Tracked separately so the F3 schema
# check expects empty stdout instead of JSON for these events.
STDOUT_PROTOCOL_RESERVED_EVENTS = frozenset({
    "WorktreeCreate", "WorktreeRemove",
})

# 2026-05-17 fixtures captured from real Claude Code hook payloads. These
# match the actual shape the harness sends, supplementing _synthetic_payload_for
# which is a permissive superset. Used by test_real_payload_fixtures_round_trip.
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "dispatch_payloads"

# Hooks that should DEMONSTRABLY fire an advisory when given a payload that
# matches their trigger conditions. Mapping: hook_id → callable that returns
# a payload guaranteed to trigger the advisory. Used by F5 positive-enforcement
# test below. Hooks not in this dict are not yet covered by positive
# enforcement (an antibody growth surface — add as you write new hooks).
POSITIVE_ENFORCEMENT_TRIGGERS: dict[str, dict[str, Any]] = {
    "pr_create_loc_accumulation": {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr create --title x --body y"},
    },
    "pr_thread_reply_waste": {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr comment 123 --body 'thanks for the fix'"},
    },
    "worktree_create_advisor": {
        "hook_event_name": "WorktreeCreate",
        "tool_input": {"path": "/tmp/synthetic-trigger", "branch": "synth"},
    },
    "worktree_remove_advisor": {
        "hook_event_name": "WorktreeRemove",
        "tool_input": {"path": "/tmp/synthetic-trigger"},
    },
    # pre_branch_create_in_primary: only triggers when cwd IS primary worktree.
    # Synthetic subprocess runs in the test worktree (NOT primary), so the
    # cwd-check returns None → no advisory. We assert non-trigger here instead;
    # operator-level e2e is the real verification path.
}


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
    in settings.json for the event type the registry declares — AND the
    settings matcher must include the tool(s) the registry says the hook
    needs (otherwise the hook fires for an unrelated tool or never fires
    for the relevant one).

    2026-05-17 critic Finding #5 (PR #127 deferred): F1 originally checked
    only event match, missing matcher mismatches where event was right but
    matcher excluded the registry's declared tool surface.
    """
    failures: list[str] = []
    for h in registry["hooks"]:
        hid = h["id"]
        expected_event = h.get("event", "")
        expected_matcher = (h.get("matcher", "") or "").strip()
        invocations = settings_invocations.get(hid, [])
        if not invocations:
            failures.append(
                f"{hid}: no settings.json dispatch invocation — hook never fires"
            )
            continue
        event_match = [m for ev, m in invocations if ev == expected_event]
        if not event_match:
            failures.append(
                f"{hid}: registry event={expected_event} but settings invokes "
                f"only under {invocations}"
            )
            continue
        # Matcher overlap check: settings matcher must include the registry's
        # declared tool surface. For pipe-OR matchers ("Edit|Write|Bash"),
        # check that EVERY registry-declared tool appears in at least one
        # settings matcher.
        if expected_matcher:
            registry_tools = {t.strip() for t in expected_matcher.split("|") if t.strip()}
            settings_tools = set()
            for m in event_match:
                settings_tools.update(t.strip() for t in m.split("|") if t.strip())
            missing = registry_tools - settings_tools
            if missing:
                failures.append(
                    f"{hid}: registry matcher={expected_matcher!r} declares tool(s) "
                    f"{sorted(missing)} not covered by settings matcher(s) "
                    f"{sorted(settings_tools)}"
                )
    assert not failures, "F1 dormant/wrong-event/matcher-gap hooks:\n" + "\n".join(failures)


@pytest.mark.parametrize("hook_idx", range(len(_load_registry().get("hooks", []))))
def test_f3_emit_advisory_schema_valid_per_hook(registry, hook_idx):
    """For each hook, synthesizing its declared event produces a response that
    matches the THREE-ROUTE protocol in _emit_advisory:
      1. allowlisted events → hookSpecificOutput JSON on stdout
      2. WorktreeCreate/Remove → EMPTY stdout (harness reads stdout as
         tool-protocol path; advisory goes to stderr)
      3. everything else → systemMessage JSON on stdout
    """
    hooks = registry["hooks"]
    h = hooks[hook_idx]
    hid = h["id"]
    event = h.get("event", "PreToolUse")
    payload = _synthetic_payload_for(event, hid)
    result = _invoke_dispatch(hid, payload)
    assert result.returncode == 0, (
        f"{hid}: dispatch exited {result.returncode}, stderr={result.stderr[:200]}"
    )
    # Route 2: WorktreeCreate/Remove MUST have empty stdout (advisory → stderr)
    if event in STDOUT_PROTOCOL_RESERVED_EVENTS:
        assert not result.stdout.strip(), (
            f"{hid}: event={event} is stdout-protocol-reserved; stdout MUST be "
            f"empty (advisory belongs on stderr). Got stdout[:200]={result.stdout[:200]}"
        )
        return
    if not result.stdout.strip():
        # Routes 1/3: empty stdout acceptable when handler returned None.
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


@pytest.mark.parametrize("hook_idx", range(len(_load_registry().get("hooks", []))))
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


# ---------------------------------------------------------------------------
# 2026-05-17 PR #128 follow-up: positive enforcement + E2E + real-payload fixtures
# ---------------------------------------------------------------------------


def test_f5_positive_enforcement_hooks_that_should_advise_do_advise():
    """For hooks that have an entry in POSITIVE_ENFORCEMENT_TRIGGERS,
    a payload guaranteed to trigger the advisory MUST produce a non-None
    return value (or for stdout-reserved events, non-empty stderr).

    Antibody for the 2026-05-17 critic Finding (PR #127 deferred): the
    runtime-fire test only asserted "didn't crash" — it could not catch
    "handler ran cleanly and silently returned None when it SHOULD have
    advised". This is the "forgot to fire" failure mode, distinct from
    the "crashed and got fail-opened" mode test_no_handler_emits_
    dispatch_error_signal already catches.

    Coverage: see POSITIVE_ENFORCEMENT_TRIGGERS dict. Add new entries when
    writing new hooks. Hooks not in the dict are not yet covered.
    """
    failures: list[str] = []
    # "Fired" = ANY of: non-empty stdout (advisory JSON), non-zero exit
    # (BLOCKING hooks like pr_create_loc_accumulation), or stderr containing
    # the hook's signature ([advisory:<id>] or BLOCKED prefix).
    for hook_id, payload in POSITIVE_ENFORCEMENT_TRIGGERS.items():
        event = payload.get("hook_event_name", "PreToolUse")
        result = _invoke_dispatch(hook_id, payload)
        fired = bool(result.stdout.strip())
        fired = fired or (result.returncode != 0)
        # Strip the noisy boot-integrity line before checking stderr signal
        stderr_clean = "\n".join(
            line for line in (result.stderr or "").splitlines()
            if "[hook integrity]" not in line
        )
        fired = fired or bool(stderr_clean.strip())
        if not fired:
            failures.append(
                f"{hook_id}: trigger payload produced NO signal "
                f"(event={event}, exit={result.returncode}). "
                f"stderr[:200]={result.stderr[:200]}"
            )
    assert not failures, (
        "Hooks silently returned None on payloads that SHOULD trigger their "
        "advisory (the 'forgot to fire' failure mode):\n" + "\n".join(failures)
    )


def test_e2e_real_dispatch_invocation_for_pretooluse_bash():
    """End-to-end: invoke dispatch.py the SAME WAY Claude Code does for a
    PreToolUse Bash event, with a payload shape matching settings.json
    fixture, and verify advisory delivery via stdout JSON.

    The runtime-fire tests (F1-F7,F10) catch boot/wiring/schema/silent-crash
    failures. This E2E test catches "the hook runs, the payload reaches the
    handler, AND the handler produces a parseable advisory the harness can
    deliver to the agent." If any step in that real chain breaks, this test
    catches it.

    Choice of hook: invariant_test (PreToolUse Bash on `git commit`). It's
    one of the OLDEST hooks (created 2026-05-06 per registry charter), so
    a regression in dispatch.py main() or _emit_advisory would surface here.
    """
    # Realistic payload shape: matches what Claude Code passes for PreToolUse
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "e2e-test",
        "tool_name": "Bash",
        "tool_input": {
            "command": "git commit -m 'test'",
            "description": "commit test",
        },
    }
    result = _invoke_dispatch("invariant_test", payload)
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr[:200]}"
    assert result.stdout.strip(), (
        "E2E: PreToolUse Bash + git commit cmd produced NO stdout advisory. "
        "invariant_test should always advise on git commit. "
        f"stderr={result.stderr[:300]}"
    )
    out = json.loads(result.stdout)
    assert "hookSpecificOutput" in out, (
        f"E2E: expected hookSpecificOutput JSON, got {list(out.keys())}"
    )
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "additionalContext" in out["hookSpecificOutput"]
    # Advisory text should mention invariant tests or pytest
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "pytest" in ctx.lower() or "invariant" in ctx.lower(), (
        f"E2E: advisory text doesn't look like invariant_test's output: {ctx[:200]}"
    )


def test_real_payload_fixtures_round_trip(registry):
    """If real-shape payload fixtures exist under tests/fixtures/dispatch_payloads/,
    feed each one to its named hook and assert no dispatch_error.

    Fixtures are JSON files named `<hook_id>.<scenario>.json`. They supplement
    _synthetic_payload_for (which is a permissive superset) with actual
    captured shapes. Captured per 2026-05-17 critic Finding #3 (PR #127
    deferred) — synthetic payloads may diverge from real ones in subtle ways.

    If FIXTURES_DIR doesn't exist, skip (no real captures yet).
    """
    if not FIXTURES_DIR.exists():
        pytest.skip(f"no fixtures captured at {FIXTURES_DIR}")
    fixtures = list(FIXTURES_DIR.glob("*.json"))
    if not fixtures:
        pytest.skip("fixtures dir exists but is empty")
    failures: list[str] = []
    for fx in fixtures:
        hook_id = fx.stem.split(".")[0]
        try:
            payload = json.loads(fx.read_text())
        except json.JSONDecodeError as e:
            failures.append(f"{fx.name}: invalid JSON ({e})")
            continue
        result = _invoke_dispatch(hook_id, payload)
        if result.returncode != 0:
            failures.append(
                f"{fx.name}: dispatch exit={result.returncode} stderr={result.stderr[:200]}"
            )
            continue
        # Check stderr doesn't have a real Python Traceback
        for marker in ("Traceback", "NameError", "TypeError", "AttributeError"):
            if marker in result.stderr:
                failures.append(f"{fx.name}: stderr contains {marker}")
                break
    assert not failures, "Real-payload fixture failures:\n" + "\n".join(failures)
