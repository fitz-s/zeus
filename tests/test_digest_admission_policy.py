"""Adversarial tests for the digest admission policy.

Exercises the Reconciler layer: forbidden-wins ordering, scope-expansion
classification, generic fallback (advisory_only, never admits caller files),
and the run_navigation admission integration.
"""
# Lifecycle: created=2026-04-25; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Lock the new admission reconciler so privilege escalation via
# substring-collision or caller-echo cannot regress.
# Reuse: When extending profiles or admission status enum, add cases here.

from __future__ import annotations

import pytest

from scripts.topology_doctor import build_digest
from scripts import topology_doctor


# ---------------------------------------------------------------------------
# Forbidden-wins ordering.
# ---------------------------------------------------------------------------

def test_forbidden_file_blocks_even_when_admitted_by_profile():
    """A path that matches both the profile's allowed_files AND its
    forbidden_files must resolve to blocked, not admitted."""
    # state/*.db is forbidden in many profiles; pick one with a settlement match.
    digest = build_digest("change settlement rounding", ["state/zeus_trades.db"])
    assert digest["admission"]["status"] == "blocked"
    assert "state/zeus_trades.db" in digest["admission"]["forbidden_hits"]


def test_forbidden_pattern_match_under_generic_profile():
    """Even with the generic fallback profile, forbidden patterns still trip."""
    digest = build_digest("misc cleanup", ["state/zeus_trades.db"])
    assert digest["admission"]["status"] == "blocked"
    assert "state/zeus_trades.db" in digest["admission"]["forbidden_hits"]


# ---------------------------------------------------------------------------
# Scope-expansion: requested file outside profile.allowed_files
# ---------------------------------------------------------------------------

def test_out_of_scope_file_returns_scope_expansion_required():
    """File evidence outside the profile's allowed_files must surface as
    scope_expansion_required, NOT silently admitted."""
    digest = build_digest(
        "change settlement rounding",
        ["src/state/lifecycle_manager.py"],  # not in settlement profile
    )
    assert digest["admission"]["status"] == "scope_expansion_required"
    assert "src/state/lifecycle_manager.py" in digest["admission"]["out_of_scope_files"]
    assert "src/state/lifecycle_manager.py" not in digest["admission"]["admitted_files"]


# ---------------------------------------------------------------------------
# Generic fallback never echoes caller files.
# ---------------------------------------------------------------------------

def test_generic_fallback_never_admits_caller_files():
    """The legacy bug: generic fallback mirrored the caller's --files into
    allowed_files, granting implicit write authority. New behavior:
    advisory_only, admitted_files == [], requested files surfaced as
    out_of_scope so the caller knows they must invoke a real profile."""
    digest = build_digest("untracked task description", ["src/foo.py", "src/bar.py"])
    assert digest["profile"] == "generic"
    assert digest["admission"]["status"] == "advisory_only"
    assert digest["admission"]["admitted_files"] == []
    # The caller's files appear as out-of-scope so they get an explicit signal.
    assert set(digest["admission"]["out_of_scope_files"]) == {"src/foo.py", "src/bar.py"}


def test_generic_fallback_with_no_files_is_advisory_only():
    digest = build_digest("describe the system", [])
    assert digest["profile"] == "generic"
    assert digest["admission"]["status"] == "advisory_only"
    assert digest["admission"]["admitted_files"] == []


# ---------------------------------------------------------------------------
# Profile match with all files inside scope: status = admitted.
# ---------------------------------------------------------------------------

def test_clean_match_returns_admitted():
    digest = build_digest(
        "change settlement rounding",
        ["src/contracts/settlement_semantics.py"],
    )
    assert digest["admission"]["status"] == "admitted"
    assert digest["admission"]["admitted_files"] == ["src/contracts/settlement_semantics.py"]
    assert digest["admission"]["forbidden_hits"] == []


def test_partial_overlap_admits_in_scope_only():
    """If the caller asks for {in_profile, out_of_profile}, only the
    in-profile path goes into admitted_files; the other surfaces as
    out_of_scope and the overall status is scope_expansion_required."""
    digest = build_digest(
        "change settlement rounding",
        ["src/contracts/settlement_semantics.py", "src/state/lifecycle_manager.py"],
    )
    assert digest["admission"]["status"] == "scope_expansion_required"
    assert "src/contracts/settlement_semantics.py" in digest["admission"]["admitted_files"]
    assert "src/state/lifecycle_manager.py" in digest["admission"]["out_of_scope_files"]


# ---------------------------------------------------------------------------
# run_navigation integration: admission denial must surface as a typed issue
# and flip ok=false.
# ---------------------------------------------------------------------------

def test_navigation_blocks_when_admission_status_is_blocked():
    payload = topology_doctor.run_navigation(
        "misc cleanup", files=["state/zeus_trades.db"], strict_health=False
    )
    assert payload["ok"] is False
    assert payload["admission"]["status"] == "blocked"
    codes = {issue["code"] for issue in payload["issues"]}
    assert "navigation_admission_blocked" in codes


def test_navigation_blocks_when_scope_expansion_required():
    payload = topology_doctor.run_navigation(
        "change settlement rounding",
        files=["src/state/lifecycle_manager.py"],
        strict_health=False,
    )
    assert payload["ok"] is False
    assert payload["admission"]["status"] == "scope_expansion_required"
    codes = {issue["code"] for issue in payload["issues"]}
    assert "navigation_scope_expansion_required" in codes


def test_navigation_advisory_only_does_not_block():
    """Generic resolution with no requested files: admission is advisory_only,
    which is NOT a write authorization but also not a blocker. The command
    succeeded; the agent simply has no admitted files."""
    payload = topology_doctor.run_navigation(
        "describe the system", files=[], strict_health=False
    )
    # Admission did not deny; ok depends only on health checks.
    assert payload["admission"]["status"] == "advisory_only"
    # admission did not contribute a typed issue:
    codes = {issue.get("code") for issue in payload["issues"]}
    assert "navigation_admission_blocked" not in codes
    assert "navigation_scope_expansion_required" not in codes


def test_navigation_admitted_does_not_block_on_admission():
    payload = topology_doctor.run_navigation(
        "change settlement rounding",
        files=["src/contracts/settlement_semantics.py"],
        strict_health=False,
    )
    assert payload["admission"]["status"] == "admitted"
    codes = {issue.get("code") for issue in payload["issues"]}
    assert "navigation_admission_blocked" not in codes
    assert "navigation_scope_expansion_required" not in codes


def test_navigation_route_context_preserves_legacy_advisory_flag():
    payload = topology_doctor.run_navigation(
        "change settlement rounding",
        files=["src/contracts/settlement_semantics.py"],
        strict_health=False,
    )
    assert payload["route_context"]["legacy_advisory"] is True
    # The new authoritative fields are present alongside the legacy mirror.
    assert "admitted_files" in payload["route_context"]
    assert "out_of_scope_files" in payload["route_context"]
    assert "forbidden_hits" in payload["route_context"]


def test_navigation_command_ok_separated_from_authorization():
    """Even when admission denies the requested files, command_ok stays True:
    the digest tool ran successfully — it just refused to authorize the writes."""
    payload = topology_doctor.run_navigation(
        "misc cleanup", files=["state/zeus_trades.db"], strict_health=False
    )
    assert payload["command_ok"] is True
    assert payload["ok"] is False  # write authorization denied
    assert payload["ok_semantics"] == "command_success_only_not_write_authorization"
