# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: Navigation Topology v2 PLAN §2.3; critic-opus C3/D1 amendment; admission_severity.yaml typed_intent_enum; sunset 2026-11-07

"""Validates the typed_intent_enum in architecture/admission_severity.yaml.

Asserts:
  1. Enum has exactly 9 values: {plan_only, create_new, modify_existing, refactor,
     audit, hygiene, hotfix, rebase_keepup, other} (D1 amendment per critic-opus C3).
  2. plan_only admits without profile match (admission shortcut per K3 — described
     in entry's description field).
  3. other is explicit fall-through (admission still applies per K1 severity tier —
     asserted via description content check).
  4. Every entry has a non-empty description.
  5. Enum IDs are unique (no duplicates).
  6. create_new carries companion_auto_admits list (K2 loop-break mechanism).
  7. Each companion_auto_admits entry has when_path_glob and also_admit fields.

Per Navigation Topology v2 PLAN §2.3 + critic-opus evidence/topology_v2_critic_opus.md
ATTACK 3 required fix (D1 resolved).
"""

from __future__ import annotations

import pathlib
import sys
import unittest.mock
from typing import Any
from unittest.mock import patch

import pytest
import yaml

REPO_ROOT_SCRIPTS = pathlib.Path(__file__).parent.parent / "scripts"
if str(REPO_ROOT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_SCRIPTS))

REPO_ROOT = pathlib.Path(__file__).parent.parent
ADMISSION_SEVERITY_YAML = REPO_ROOT / "architecture" / "admission_severity.yaml"

# Canonical 9-value enum per D1 amendment (critic-opus ATTACK 3 resolved in PLAN §0.5)
EXPECTED_ENUM_VALUES = {
    "plan_only",
    "create_new",
    "modify_existing",
    "refactor",
    "audit",
    "hygiene",
    "hotfix",
    "rebase_keepup",
    "other",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_intent_enum() -> list[dict]:
    assert ADMISSION_SEVERITY_YAML.exists(), (
        f"architecture/admission_severity.yaml not found at {ADMISSION_SEVERITY_YAML}"
    )
    with ADMISSION_SEVERITY_YAML.open() as f:
        data = yaml.safe_load(f)
    return data.get("typed_intent_enum", [])


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------

def test_enum_has_exactly_9_values() -> None:
    """Enum must have exactly 9 values per critic-opus D1 amendment.

    Values: plan_only, create_new, modify_existing, refactor, audit, hygiene,
    hotfix, rebase_keepup, other.
    """
    entries = _load_intent_enum()
    ids = {e["id"] for e in entries if isinstance(e, dict) and "id" in e}
    assert ids == EXPECTED_ENUM_VALUES, (
        f"typed_intent_enum IDs do not match expected 9 values.\n"
        f"  Expected: {sorted(EXPECTED_ENUM_VALUES)}\n"
        f"  Got:      {sorted(ids)}\n"
        f"  Missing:  {sorted(EXPECTED_ENUM_VALUES - ids)}\n"
        f"  Extra:    {sorted(ids - EXPECTED_ENUM_VALUES)}"
    )


def test_enum_ids_are_unique() -> None:
    """No duplicate IDs in the enum."""
    entries = _load_intent_enum()
    ids = [e["id"] for e in entries if isinstance(e, dict) and "id" in e]
    duplicates = [i for i in set(ids) if ids.count(i) > 1]
    assert not duplicates, (
        f"Duplicate typed_intent_enum IDs found: {duplicates}"
    )


def test_plan_only_admits_without_profile_match() -> None:
    """plan_only entry must describe admission without profile match (K3 shortcut).

    Per PLAN §2.3: 'plan_only' admitted directly without profile match for direct
    admission. The description must explicitly reference this shortcut.
    """
    entries = _load_intent_enum()
    plan_only = next((e for e in entries if e.get("id") == "plan_only"), None)
    assert plan_only is not None, "plan_only entry not found in typed_intent_enum"

    desc = plan_only.get("description", "")
    assert desc, "plan_only entry has empty description"

    # Description must indicate it admits without requiring profile match
    # (checking for key phrase variants used in the PLAN §2.3 and §0.5)
    desc_lower = desc.lower()
    shortcut_signals = [
        "without profile",
        "admission shortcut",
        "directly without",
        "no profile match",
        "short-circuits",
        "short circuits",
    ]
    assert any(sig in desc_lower for sig in shortcut_signals), (
        f"plan_only description does not mention admission shortcut / profile bypass.\n"
        f"Description: {desc!r}\n"
        f"Expected one of the signals: {shortcut_signals}"
    )


def test_other_is_explicit_fallthrough_with_admission_applies() -> None:
    """other entry must be explicit fall-through AND state admission still applies (K1).

    Per PLAN §0.5 C3 resolution: 'other is explicit fall-through (admission still
    applies per K1 severity tier).' Description must convey both.
    """
    entries = _load_intent_enum()
    other = next((e for e in entries if e.get("id") == "other"), None)
    assert other is not None, "other entry not found in typed_intent_enum"

    desc = other.get("description", "")
    assert desc, "other entry has empty description"

    desc_lower = desc.lower()

    # Must indicate fall-through
    fallthrough_signals = [
        "fall-through",
        "fallthrough",
        "fall through",
        "does not match",
        "none of the",
    ]
    assert any(sig in desc_lower for sig in fallthrough_signals), (
        f"other description does not indicate fall-through semantics.\n"
        f"Description: {desc!r}"
    )

    # Must indicate admission still applies (K1 severity tier)
    admission_applies_signals = [
        "admission still applies",
        "admission applies",
        "k1",
        "severity tier",
        "gates still apply",
    ]
    assert any(sig in desc_lower for sig in admission_applies_signals), (
        f"other description does not state that admission still applies per K1.\n"
        f"Description: {desc!r}"
    )


def test_create_new_has_companion_auto_admits() -> None:
    """create_new must carry companion_auto_admits list (K2 loop-break).

    Per PLAN §2.3: create_new auto-admits the manifest companion required by
    mesh-maintenance rule. The companion_auto_admits field implements K2.
    """
    entries = _load_intent_enum()
    create_new = next((e for e in entries if e.get("id") == "create_new"), None)
    assert create_new is not None, "create_new entry not found in typed_intent_enum"

    companions = create_new.get("companion_auto_admits", [])
    assert companions, (
        "create_new entry missing companion_auto_admits list. "
        "K2 companion-loop-break requires this field."
    )


def test_create_new_companion_entries_have_required_fields() -> None:
    """Each companion_auto_admits entry must have when_path_glob and also_admit."""
    entries = _load_intent_enum()
    create_new = next((e for e in entries if e.get("id") == "create_new"), None)
    assert create_new is not None, "create_new entry not found"

    companions = create_new.get("companion_auto_admits", [])
    for i, comp in enumerate(companions):
        assert "when_path_glob" in comp, (
            f"companion_auto_admits[{i}] missing 'when_path_glob' field: {comp}"
        )
        assert "also_admit" in comp, (
            f"companion_auto_admits[{i}] missing 'also_admit' field: {comp}"
        )


# ---------------------------------------------------------------------------
# Per-entry completeness
# ---------------------------------------------------------------------------

def _enum_entry_params() -> list[tuple[str, dict]]:
    if not ADMISSION_SEVERITY_YAML.exists():
        return []
    with ADMISSION_SEVERITY_YAML.open() as f:
        data = yaml.safe_load(f)
    entries = data.get("typed_intent_enum", [])
    return [(e.get("id", f"entry_{i}"), e) for i, e in enumerate(entries)]


@pytest.mark.parametrize("intent_id,entry", _enum_entry_params(),
                         ids=[p[0] for p in _enum_entry_params()])
def test_enum_entry_has_nonempty_description(intent_id: str, entry: dict) -> None:
    """Every enum entry must have a non-empty description string."""
    desc = entry.get("description", "")
    assert desc and desc.strip(), (
        f"typed_intent_enum entry {intent_id!r} has empty or missing description."
    )


@pytest.mark.parametrize("intent_id,entry", _enum_entry_params(),
                         ids=[p[0] for p in _enum_entry_params()])
def test_enum_entry_has_sunset_date(intent_id: str, entry: dict) -> None:
    """Every enum entry must have a sunset_date field (ANTI_DRIFT_CHARTER §M3)."""
    assert "sunset_date" in entry, (
        f"typed_intent_enum entry {intent_id!r} missing sunset_date. "
        "Per ANTI_DRIFT_CHARTER §M3 every artifact must carry a machine-readable sunset."
    )
    assert entry["sunset_date"] is not None, (
        f"typed_intent_enum entry {intent_id!r} has null sunset_date."
    )


# ---------------------------------------------------------------------------
# PR #72 Codex P1 round-2: blocked typed-intent paths must not produce admitted
# ---------------------------------------------------------------------------

def _make_base_admission(status: str = "advisory_only") -> dict[str, Any]:
    return {
        "status": status,
        "admitted_files": [],
        "out_of_scope_files": [],
        "forbidden_hits": [],
        "decision_basis": {"why": [], "selected_by": "test"},
    }


def _shortcut(
    admission: dict[str, Any],
    requested: list[str],
    intent: str,
    blocked_globs: tuple[str, ...] = ("src/execution/**",),
) -> dict[str, Any]:
    """Call _apply_typed_intent_shortcut with patched blocked globs."""
    import topology_doctor_digest as tdd

    intent_map = {"plan_only": blocked_globs, "audit": blocked_globs}
    with patch.object(tdd, "_load_typed_intent_blocked_globs", return_value=intent_map):
        return tdd._apply_typed_intent_shortcut(admission, requested, intent)


def test_all_blocked_paths_produce_advisory_only_not_admitted() -> None:
    """When ALL requested paths are blocked by intent, status MUST be advisory_only.

    PR #72 Codex P1 round-2: _apply_typed_intent_shortcut must not set status=admitted
    when blocked_by_intent is non-empty and admitted_files is empty.
    """
    admission = _make_base_admission("advisory_only")
    result = _shortcut(
        admission,
        requested=["src/execution/live_executor.py"],
        intent="plan_only",
    )
    assert result["status"] != "admitted", (
        f"status must not be 'admitted' when all paths are blocked; got {result['status']!r}"
    )
    assert result["status"] == "advisory_only", (
        f"expected advisory_only for all-blocked; got {result['status']!r}"
    )
    assert result.get("admitted_files") == [], (
        "admitted_files must be empty when all paths blocked"
    )


def test_mixed_paths_produce_non_admitted_status() -> None:
    """When some paths admitted and some blocked, status must not be admitted.

    PR #72 Codex P1 round-2: partial admission with blocked_by_intent non-empty
    must not look like full edit authorization.
    """
    admission = _make_base_admission("advisory_only")
    result = _shortcut(
        admission,
        requested=["docs/operations/AGENTS.md", "src/execution/live_executor.py"],
        intent="plan_only",
    )
    assert result["status"] != "admitted", (
        f"status must not be 'admitted' in mixed state; got {result['status']!r}"
    )
    # docs path admitted, src path blocked
    assert "docs/operations/AGENTS.md" in result.get("admitted_files", []), (
        "docs path must appear in admitted_files"
    )
    assert "src/execution/live_executor.py" in result.get("out_of_scope_files", []), (
        "blocked src path must appear in out_of_scope_files"
    )


def test_no_blocked_paths_still_admits() -> None:
    """When no paths are blocked, status remains admitted (regression guard).

    Ensures the fix does not break the clean-path case.
    """
    admission = _make_base_admission("advisory_only")
    result = _shortcut(
        admission,
        requested=["docs/operations/AGENTS.md"],
        intent="plan_only",
        blocked_globs=("src/execution/**",),  # docs/ does not match
    )
    assert result["status"] == "admitted", (
        f"expected admitted when no paths blocked; got {result['status']!r}"
    )
    assert "docs/operations/AGENTS.md" in result.get("admitted_files", []), (
        "docs path must be in admitted_files"
    )
