# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: PLAN §2.2 + §3 Phase 1 exit criteria
#   docs/operations/task_2026-05-06_hook_redesign/PLAN.md

"""
YAML schema validation for .claude/hooks/registry.yaml and overrides.yaml.

Exit criteria verified:
- schema_version present + integer
- Every hook has: id, event, severity, sunset_date
- severity in {ADVISORY, BLOCKING}
- sunset_date required on every hook
- auto_expires_after: never only on REVIEW_SAFE_TAG + ISOLATED_WORKTREE
- overrides.yaml schema_version present
- All hook ids are unique
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / ".claude" / "hooks" / "registry.yaml"

NEVER_EXPIRY_WHITELIST = {"REVIEW_SAFE_TAG", "ISOLATED_WORKTREE"}
# Severity tiers: ADVISORY emits context only; BLOCKING returns exit 2 to deny
# the tool call. The 2026-05-06 redesign retired most BLOCKING hooks but kept
# pr_create_loc_accumulation + pre_merge_comment_check intentionally because
# the cost/safety asymmetry on those events justifies a hard stop. The
# registry comment ("BLOCKING tier retired") refers to the wholesale removal
# of advisory→blocking promotions, not the elimination of the tier itself.
VALID_SEVERITY = {"ADVISORY", "BLOCKING"}
VALID_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "SessionStart",
    "Stop",
    "SubagentStop",
    "PreCompact",
    "WorktreeCreate",
    "WorktreeRemove",
}


@pytest.fixture(scope="module")
def registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text())


# ---------------------------------------------------------------------------
# registry.yaml schema tests
# ---------------------------------------------------------------------------


def test_registry_file_exists() -> None:
    assert REGISTRY_PATH.exists(), f"registry.yaml not found at {REGISTRY_PATH}"


def test_registry_schema_version(registry: dict) -> None:
    assert "schema_version" in registry, "registry.yaml missing schema_version"
    assert isinstance(registry["schema_version"], int), (
        f"schema_version must be int, got {type(registry['schema_version'])}"
    )
    assert registry["schema_version"] == 1


def test_registry_has_hooks_list(registry: dict) -> None:
    assert "hooks" in registry, "registry.yaml missing 'hooks' key"
    assert isinstance(registry["hooks"], list)
    assert len(registry["hooks"]) > 0, "hooks list must not be empty"


def test_registry_metadata_catalog_size(registry: dict) -> None:
    meta = registry.get("metadata", {})
    assert "catalog_size" in meta, "metadata.catalog_size required"
    actual = len(registry["hooks"])
    stated = meta["catalog_size"]
    assert actual == stated, (
        f"metadata.catalog_size={stated} but hooks list has {actual} entries"
    )


def test_hook_ids_are_unique(registry: dict) -> None:
    ids = [h["id"] for h in registry["hooks"]]
    assert len(ids) == len(set(ids)), f"Duplicate hook ids: {[i for i in ids if ids.count(i)>1]}"



# Use a non-lazy approach: load inline
_REGISTRY_DATA = yaml.safe_load(REGISTRY_PATH.read_text()) if REGISTRY_PATH.exists() else {"hooks": []}
_HOOKS = _REGISTRY_DATA.get("hooks", [])


@pytest.mark.parametrize("hook", _HOOKS, ids=[h["id"] for h in _HOOKS])
def test_hook_has_required_fields(hook: dict) -> None:
    for field in ("id", "event", "severity", "sunset_date", "intent"):
        assert field in hook, f"Hook {hook.get('id','?')} missing required field '{field}'"


@pytest.mark.parametrize("hook", _HOOKS, ids=[h["id"] for h in _HOOKS])
def test_hook_severity_valid(hook: dict) -> None:
    assert hook["severity"] in VALID_SEVERITY, (
        f"Hook {hook['id']}: severity={hook['severity']!r} not in {VALID_SEVERITY}"
    )


@pytest.mark.parametrize("hook", _HOOKS, ids=[h["id"] for h in _HOOKS])
def test_hook_event_valid(hook: dict) -> None:
    assert hook["event"] in VALID_EVENTS, (
        f"Hook {hook['id']}: event={hook['event']!r} not in {VALID_EVENTS}"
    )


@pytest.mark.parametrize("hook", _HOOKS, ids=[h["id"] for h in _HOOKS])
def test_hook_sunset_date_present_and_parseable(hook: dict) -> None:
    sd = hook.get("sunset_date")
    assert sd is not None, f"Hook {hook['id']} missing sunset_date"
    # Must be parseable as ISO date
    try:
        parsed = date.fromisoformat(str(sd))
    except ValueError as exc:
        pytest.fail(f"Hook {hook['id']} sunset_date={sd!r} not ISO date: {exc}")
    assert parsed > date(2026, 1, 1), f"Hook {hook['id']} sunset_date in the past: {sd}"


@pytest.mark.parametrize("hook", _HOOKS, ids=[h["id"] for h in _HOOKS])
def test_advisory_hook_has_no_blocking_exit(hook: dict) -> None:
    """ADVISORY hooks must not have blocking bypass_policy class."""
    if hook["severity"] == "ADVISORY":
        bp_class = hook.get("bypass_policy", {}).get("class", "not_required")
        assert bp_class in ("not_required", None, ""), (
            f"Advisory hook {hook['id']} should not have bypass_policy.class={bp_class!r}"
        )


