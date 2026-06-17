# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/operations/current/workspace-routing-redesign/PLAN.md §6, §7 (durability vectors #6/#7/#10), §12 S1
#
# Antibody for the workspace write-router. Makes "router removed / broken /
# over-broad" a FAILING test rather than a silent regression (PLAN §7 #6, #7):
#   - a high-precision scratch NEW Write must be updatedInput-rerouted to .omx/
#   - an Edit must NEVER be rerouted (R13 — old_string desync would corrupt edits)
#   - a normal new file must be left alone (anti-data-loss R16 — never buried)
#   - a cross-tenant worktree write must HARD-STOP (exit 2)
#   - route_write must be registered BLOCKING-tier on a Write matcher
#   - dispatch.py boot self-test must find a handler for every registry hook

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / ".claude" / "hooks"
DISPATCH = HOOKS / "dispatch.py"
sys.path.insert(0, str(HOOKS))

import route_write  # noqa: E402


def _check(tool_name: str, file_path: str, *, edit: bool = False):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": (
            {"file_path": file_path, "old_string": "a", "new_string": "b"}
            if edit
            else {"file_path": file_path, "content": "x"}
        ),
    }
    return route_write._run_advisory_check_route_write(payload)


# --- handler-level (the contract route_write must keep) ----------------------

def test_scratch_new_write_is_rerouted_to_omx():
    res = _check("Write", "wf_demo.js")
    assert isinstance(res, dict), "scratch Write must SILENT-ROUTE (dict return)"
    assert res["updatedInput"]["file_path"] == ".omx/wf_demo.js"


def test_scratch_underscore_form_rerouted():
    res = _check("Write", "docs/foo_scratch.md")
    assert isinstance(res, dict)
    assert res["updatedInput"]["file_path"] == ".omx/foo_scratch.md"


def test_edit_is_never_rerouted():
    # R13: even a scratch-shaped path must NOT be rerouted on an Edit.
    assert _check("Edit", "wf_demo.js", edit=True) is None
    assert _check("MultiEdit", "wf_demo.js", edit=True) is None


def test_normal_new_file_left_alone():
    # Anti-data-loss (R16): a real-looking file lacking a home is NOT buried.
    assert _check("Write", "src/some_new_module_zzz.py") is None
    assert _check("Write", "docs/reference/some_topic_zzz.md") is None


def test_already_in_omx_is_noop():
    assert _check("Write", ".omx/wf_demo.js") is None


def test_cross_tenant_worktree_write_blocks():
    res = _check("Write", ".claude/worktrees/agent-OTHER/foo.py")
    assert res == route_write._BLOCK_SENTINEL


def test_loose_plan_nudged():
    # A work-artifact dropped at a loose location -> NUDGE (str), never silent.
    res = _check("Write", "docs/operations/some_PLAN.md")
    assert isinstance(res, str) and "by-work" in res


def test_loose_report_at_repo_root_nudged():
    res = _check("Write", "report.md")
    assert isinstance(res, str) and "work-artifact" in res


def test_workartifact_in_workfolder_not_nudged():
    # Already under a by-work subfolder -> correct -> no-op.
    assert _check("Write", "docs/operations/current/my-work/PLAN.md") is None


def test_workartifact_in_legacy_bykind_left_alone():
    # Legacy by-kind dir is recognized (migration regroups it) -> no nudge churn.
    assert _check("Write", "docs/operations/current/plans/foo.md") is None


def test_nonwork_md_at_loose_location_not_nudged():
    # A normal doc that isn't a work-artifact shape is left alone (no false nudge).
    assert _check("Write", "docs/operations/notes.md") is None


# --- S2-full: slug-match silent-route into an EXISTING by-work folder ---------
# Uses the real committed folder docs/operations/current/workspace-routing-redesign/
# (has PLAN.md + scope.yaml, no report.md) as the resolution target.

_WORK = "workspace-routing-redesign"


def test_loose_report_slugmatch_silent_routes_to_existing_work():
    res = _check("Write", f"docs/operations/{_WORK}_REPORT.md")
    assert isinstance(res, dict), "should silent-route into the matching work folder"
    assert res["updatedInput"]["file_path"] == f"docs/operations/current/{_WORK}/report.md"


def test_loose_plan_slugmatch_clobber_nudges_not_overwrites():
    # PLAN.md already exists in the work folder -> never clobber -> NUDGE (str).
    res = _check("Write", f"docs/operations/{_WORK}_PLAN.md")
    assert isinstance(res, str) and "by-work" in res


def test_loose_workartifact_no_matching_work_nudges():
    res = _check("Write", "docs/operations/nonexistent-xyz_PLAN.md")
    assert isinstance(res, str) and "nudge" in res.lower()


def test_crash_fails_open():
    # A malformed payload must never raise / never block.
    assert route_write._run_advisory_check_route_write({"tool_input": None}) is None
    assert route_write._run_advisory_check_route_write({}) is None


# --- integration (the wired path through dispatch.py) ------------------------

def _dispatch(payload: dict):
    proc = subprocess.run(
        [sys.executable, str(DISPATCH), "route_write"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    return proc


def test_dispatch_emits_updatedinput_for_scratch():
    proc = _dispatch({
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "wf_demo.js", "content": "x"},
    })
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["updatedInput"]["file_path"] == ".omx/wf_demo.js"


def test_dispatch_blocks_cross_tenant_exit2():
    proc = _dispatch({
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": ".claude/worktrees/agent-OTHER/foo.py", "content": "x"},
    })
    assert proc.returncode == 2


# --- registry / boot (the deletion-protection guards) -----------------------

def test_route_write_registered_blocking_on_write_matcher():
    import yaml  # type: ignore
    reg = yaml.safe_load((HOOKS / "registry.yaml").read_text())
    hook = next((h for h in reg["hooks"] if h["id"] == "route_write"), None)
    assert hook is not None, "route_write missing from registry.yaml (deletion guard)"
    assert hook["severity"] == "BLOCKING"
    assert hook["matcher"] == "Write"
    assert hook["event"] == "PreToolUse"


def test_boot_self_test_finds_all_handlers():
    proc = subprocess.run(
        [sys.executable, str(DISPATCH), "boot_self_test_only"],
        capture_output=True, text=True,
    )
    assert "no handler for hook id" not in proc.stderr
    assert "all" in proc.stderr and "have handlers" in proc.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
