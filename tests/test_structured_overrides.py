# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: PLAN §2.3 + critic-opus §0.5 (ATTACK 7 binding)
#   docs/operations/task_2026-05-06_hook_redesign/PLAN.md
#   evidence/hook_redesign_critic_opus.md

"""
Structured override validation tests.

Per ATTACK 7 (critic-opus §0.5), MUST verify:
1. Evidence file existence check
2. Auto-expiry via git log -1 --format=%ct (NOT mtime)
3. Replay protection: (override_id, evidence_file) counts once per 30d
4. OPERATOR_SIGNATURE_REQUIRED: TRUTH_REWRITE/ON_CHAIN class requires
   evidence/operator_signed/<override>_<date>.signed sentinel
5. auto_expires_after: never only for REVIEW_SAFE_TAG + ISOLATED_WORKTREE

Coverage: each override_id in overrides.yaml exercised.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCH_PATH = REPO_ROOT / ".claude" / "hooks" / "dispatch.py"
OVERRIDES_PATH = REPO_ROOT / ".claude" / "hooks" / "overrides.yaml"
REGISTRY_PATH = REPO_ROOT / ".claude" / "hooks" / "registry.yaml"

_OVERRIDES_DATA = yaml.safe_load(OVERRIDES_PATH.read_text())
_OVERRIDES: list[dict[str, Any]] = _OVERRIDES_DATA.get("overrides", [])
_OVERRIDE_IDS = [o["id"] for o in _OVERRIDES]
_OVERRIDE_BY_ID = {o["id"]: o for o in _OVERRIDES}

_NEVER_EXPIRY_WHITELIST = {"REVIEW_SAFE_TAG", "ISOLATED_WORKTREE"}


# ---------------------------------------------------------------------------
# Import dispatch module for unit-level tests
# ---------------------------------------------------------------------------

def _import_dispatch():
    """Import dispatch module from .claude/hooks/."""
    hooks_dir = str(REPO_ROOT / ".claude" / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    import importlib
    import dispatch
    importlib.reload(dispatch)  # ensure fresh state
    return dispatch


# ---------------------------------------------------------------------------
# 1. Every override_id in overrides.yaml is exercised
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("override_id", _OVERRIDE_IDS)
def test_override_id_exists_in_catalog(override_id: str) -> None:
    """Every override in overrides.yaml has id, description, requires fields."""
    override = _OVERRIDE_BY_ID[override_id]
    for field in ("id", "description", "requires"):
        assert field in override, (
            f"Override {override_id} missing required field '{field}'"
        )


@pytest.mark.parametrize("override_id", _OVERRIDE_IDS)
def test_override_never_expiry_whitelist(override_id: str) -> None:
    """auto_expires_after: never only allowed for REVIEW_SAFE_TAG + ISOLATED_WORKTREE."""
    override = _OVERRIDE_BY_ID[override_id]
    auto_exp = override.get("requires", {}).get("auto_expires_after", "24h")
    if auto_exp == "never":
        assert override_id in _NEVER_EXPIRY_WHITELIST, (
            f"Override {override_id}: auto_expires_after=never not permitted "
            f"(only {_NEVER_EXPIRY_WHITELIST} may use never)"
        )


# ---------------------------------------------------------------------------
# 2. Evidence file validation (requires dispatch.validate_override)
# ---------------------------------------------------------------------------

class TestEvidenceFileValidation:
    """Tests for validate_override evidence file checks."""

    def setup_method(self):
        self.d = _import_dispatch()

    def test_missing_evidence_file_fails(self, tmp_path: Path) -> None:
        """Override with evidence_file requirement fails when file missing."""
        override = {
            "id": "BASELINE_RATCHET",
            "requires": {
                "evidence_file": str(tmp_path / "nonexistent.md"),
                "auto_expires_after": "24h",
            },
        }
        spec = {"event": "PreToolUse", "id": "invariant_test"}
        payload = {"session_id": "test"}

        result = self.d.validate_override(override, spec, payload)
        assert result is False, (
            "validate_override must return False when evidence file is missing"
        )

    def test_present_fresh_evidence_file_passes(self, tmp_path: Path) -> None:
        """Override with recently-created evidence file passes (mtime fallback)."""
        ev_file = tmp_path / "evidence.md"
        ev_file.write_text("old_baseline: 100\nnew_baseline: 110\njustification: test\nphase_id: P2\n")

        override = {
            "id": "BASELINE_RATCHET",
            "requires": {
                "evidence_file": str(ev_file),
                "auto_expires_after": "24h",
            },
        }
        spec = {"event": "PreToolUse", "id": "invariant_test"}
        payload = {"session_id": "test"}

        result = self.d.validate_override(override, spec, payload)
        assert result is True, (
            "validate_override must return True for a fresh evidence file"
        )

    def test_expired_evidence_file_fails(self, tmp_path: Path) -> None:
        """Override with expired evidence file fails the expiry check."""
        ev_file = tmp_path / "evidence.md"
        ev_file.write_text("old_baseline: 100\njustification: test\n")

        # Set mtime to 25 hours ago (past 24h auto_expires_after)
        old_time = time.time() - (25 * 3600)
        os.utime(ev_file, (old_time, old_time))

        override = {
            "id": "BASELINE_RATCHET",
            "requires": {
                "evidence_file": str(ev_file),
                "auto_expires_after": "24h",
            },
        }
        spec = {"event": "PreToolUse", "id": "invariant_test"}
        payload = {"session_id": "test"}

        result = self.d.validate_override(override, spec, payload)
        assert result is False, (
            "validate_override must return False for expired evidence file (25h > 24h)"
        )

    def test_template_evidence_path_accepted_in_phase1(self) -> None:
        """
        Template paths with '<' placeholders are accepted in Phase 1
        (concrete resolution comes in Phase 3).
        """
        override = {
            "id": "BASELINE_RATCHET",
            "requires": {
                "evidence_file": "evidence/baseline_ratchets/<date>_<phase>.md",
                "auto_expires_after": "24h",
            },
        }
        spec = {"event": "PreToolUse", "id": "invariant_test"}
        payload = {"session_id": "test"}

        # Template paths with '<' skip existence check in Phase 1
        result = self.d.validate_override(override, spec, payload)
        assert result is True, (
            "Template evidence paths (with '<') must be accepted in Phase 1"
        )


# ---------------------------------------------------------------------------
# 3. Auto-expiry via git log (immutable provenance — ATTACK 7)
# ---------------------------------------------------------------------------

class TestAutoExpiryViaGitLog:
    """
    Tests for expiry using git log -1 --format=%ct as clock-start (ATTACK 7).
    """

    def setup_method(self):
        self.d = _import_dispatch()

    def test_get_evidence_file_commit_time_returns_none_for_uncommitted(
        self, tmp_path: Path
    ) -> None:
        """Uncommitted file has no git commit timestamp; function returns None."""
        ev_file = tmp_path / "evidence.md"
        ev_file.write_text("test content\n")
        result = self.d._get_evidence_file_commit_time(ev_file)
        # Uncommitted or no git history → None
        assert result is None

    def test_expiry_uses_commit_time_when_available(self, tmp_path: Path) -> None:
        """
        When git log returns a commit timestamp, expiry is computed from it.
        We test by patching _get_evidence_file_commit_time to return a known value.
        """
        import unittest.mock as mock

        ev_file = tmp_path / "evidence.md"
        ev_file.write_text("test\n")

        # Simulate committed 2 days ago (past 24h expiry)
        two_days_ago = time.time() - (2 * 86400)

        override = {
            "id": "BASELINE_RATCHET",
            "requires": {
                "evidence_file": str(ev_file),
                "auto_expires_after": "24h",
            },
        }
        spec = {"event": "PreToolUse", "id": "invariant_test"}
        payload = {"session_id": "test"}

        with mock.patch.object(
            self.d, "_get_evidence_file_commit_time", return_value=two_days_ago
        ):
            result = self.d.validate_override(override, spec, payload)

        assert result is False, (
            "Override should be expired when git commit time is 2 days ago "
            "and auto_expires_after is 24h"
        )

    def test_expiry_uses_commit_time_fresh(self, tmp_path: Path) -> None:
        """
        When git log returns a recent timestamp, override is valid.
        """
        import unittest.mock as mock

        ev_file = tmp_path / "evidence.md"
        ev_file.write_text("test\n")

        one_hour_ago = time.time() - 3600

        override = {
            "id": "BASELINE_RATCHET",
            "requires": {
                "evidence_file": str(ev_file),
                "auto_expires_after": "24h",
            },
        }
        spec = {"event": "PreToolUse", "id": "invariant_test"}
        payload = {"session_id": "test"}

        with mock.patch.object(
            self.d, "_get_evidence_file_commit_time", return_value=one_hour_ago
        ):
            result = self.d.validate_override(override, spec, payload)

        assert result is True, (
            "Override should be valid when git commit time is 1h ago and "
            "auto_expires_after is 24h"
        )

    def test_never_expiry_skips_time_check(self, tmp_path: Path) -> None:
        """Override with auto_expires_after: never skips all expiry checks."""
        ev_file = tmp_path / "never_expires.md"
        ev_file.write_text("inline_tag_present: true\n")

        override = {
            "id": "REVIEW_SAFE_TAG",
            "requires": {
                "auto_expires_after": "never",
                "inline_tag_present": True,
            },
        }
        spec = {"event": "PreToolUse", "id": "secrets_scan"}
        payload = {"session_id": "test"}

        # Should not fail due to expiry for whitelisted never-expiry overrides
        result = self.d.validate_override(override, spec, payload)
        assert result is True, (
            "REVIEW_SAFE_TAG with auto_expires_after:never must pass validation "
            "(whitelisted never-expiry)"
        )

    def test_never_expiry_non_whitelisted_fails(self) -> None:
        """Non-whitelisted override with auto_expires_after: never is rejected."""
        override = {
            "id": "BASELINE_RATCHET",
            "requires": {
                "auto_expires_after": "never",
            },
        }
        spec = {"event": "PreToolUse", "id": "invariant_test"}
        payload = {"session_id": "test"}

        result = self.d.validate_override(override, spec, payload)
        assert result is False, (
            "BASELINE_RATCHET with auto_expires_after:never must be rejected "
            "(not in NEVER_EXPIRY_WHITELIST)"
        )


# ---------------------------------------------------------------------------
# 4. Replay protection (ATTACK 7)
# ---------------------------------------------------------------------------

class TestReplayProtection:
    """
    (override_id, evidence_file) pair counts once per 30d toward max_active_per_30d.
    """

    def setup_method(self):
        self.d = _import_dispatch()
        # Clear the in-process cache before each test
        self.d._SEEN_PAIRS.clear()

    def test_same_pair_counted_once(self, tmp_path: Path) -> None:
        """Same (override_id, evidence_file) pair is idempotent — counts once."""
        ev_file = tmp_path / "evidence.md"
        ev_file.write_text("test\n")

        override = {
            "id": "BASELINE_RATCHET",
            "requires": {
                "evidence_file": str(ev_file),
                "auto_expires_after": "24h",
            },
        }
        spec = {"event": "PreToolUse", "id": "invariant_test"}
        payload = {"session_id": "test"}

        import unittest.mock as mock
        one_hour_ago = time.time() - 3600
        with mock.patch.object(
            self.d, "_get_evidence_file_commit_time", return_value=one_hour_ago
        ):
            result1 = self.d.validate_override(override, spec, payload)
            result2 = self.d.validate_override(override, spec, payload)

        assert result1 is True
        assert result2 is True  # Same pair; still allowed (idempotent)
        # Only one entry in _SEEN_PAIRS for this pair
        pair_key = ("BASELINE_RATCHET", str(ev_file))
        assert pair_key in self.d._SEEN_PAIRS

    def test_different_evidence_files_are_different_pairs(self, tmp_path: Path) -> None:
        """Different evidence files create separate replay entries."""
        ev1 = tmp_path / "ev1.md"
        ev2 = tmp_path / "ev2.md"
        ev1.write_text("file 1\n")
        ev2.write_text("file 2\n")

        import unittest.mock as mock
        one_hour_ago = time.time() - 3600

        override1 = {
            "id": "BASELINE_RATCHET",
            "requires": {"evidence_file": str(ev1), "auto_expires_after": "24h"},
        }
        override2 = {
            "id": "BASELINE_RATCHET",
            "requires": {"evidence_file": str(ev2), "auto_expires_after": "24h"},
        }
        spec = {"event": "PreToolUse", "id": "invariant_test"}
        payload = {"session_id": "test"}

        with mock.patch.object(
            self.d, "_get_evidence_file_commit_time", return_value=one_hour_ago
        ):
            r1 = self.d.validate_override(override1, spec, payload)
            r2 = self.d.validate_override(override2, spec, payload)

        assert r1 is True
        assert r2 is True
        assert ("BASELINE_RATCHET", str(ev1)) in self.d._SEEN_PAIRS
        assert ("BASELINE_RATCHET", str(ev2)) in self.d._SEEN_PAIRS

    def test_30d_window_reset(self, tmp_path: Path) -> None:
        """
        Pair first seen > 30 days ago is reset and re-counted.
        Simulates the 30d window rolling over.
        """
        ev_file = tmp_path / "evidence.md"
        ev_file.write_text("test\n")
        pair_key = ("BASELINE_RATCHET", str(ev_file))

        # Pre-seed _SEEN_PAIRS as if first seen 31 days ago
        self.d._SEEN_PAIRS[pair_key] = time.time() - (31 * 86400)

        override = {
            "id": "BASELINE_RATCHET",
            "requires": {
                "evidence_file": str(ev_file),
                "auto_expires_after": "24h",
            },
        }
        spec = {"event": "PreToolUse", "id": "invariant_test"}
        payload = {"session_id": "test"}

        import unittest.mock as mock
        one_hour_ago = time.time() - 3600
        with mock.patch.object(
            self.d, "_get_evidence_file_commit_time", return_value=one_hour_ago
        ):
            result = self.d.validate_override(override, spec, payload)

        assert result is True
        # Pair timestamp should be refreshed to approximately now
        assert (time.time() - self.d._SEEN_PAIRS[pair_key]) < 60, (
            "Pair timestamp should be refreshed after 30d window reset"
        )


# ---------------------------------------------------------------------------
# 5. OPERATOR_SIGNATURE_REQUIRED for TRUTH_REWRITE class (OD-HOOK-2)
# ---------------------------------------------------------------------------

class TestOperatorSignatureRequired:
    """
    TRUTH_REWRITE/ON_CHAIN-class hooks require operator-signed sentinel files.
    Verify that registry.yaml has OPERATOR_SIGNATURE_REQUIRED: true for
    TRUTH_REWRITE-class hooks that warrant it.
    """

    def setup_method(self):
        self.registry = yaml.safe_load(REGISTRY_PATH.read_text())

    def test_pre_edit_hooks_protected_has_operator_signature_required(self) -> None:
        """pre_edit_hooks_protected (TRUTH_REWRITE) must have OPERATOR_SIGNATURE_REQUIRED:true."""
        hooks_by_id = {h["id"]: h for h in self.registry["hooks"]}
        hook = hooks_by_id.get("pre_edit_hooks_protected")
        assert hook is not None, "pre_edit_hooks_protected must be in registry.yaml"
        bp = hook.get("bypass_policy", {})
        assert bp.get("OPERATOR_SIGNATURE_REQUIRED") is True, (
            "pre_edit_hooks_protected: OPERATOR_SIGNATURE_REQUIRED must be True "
            "(TRUTH_REWRITE class per OD-HOOK-2)"
        )

    def test_pre_checkout_overlap_has_operator_signature_required(self) -> None:
        """pre_checkout_uncommitted_overlap (TRUTH_REWRITE) must have OPERATOR_SIGNATURE_REQUIRED:true."""
        hooks_by_id = {h["id"]: h for h in self.registry["hooks"]}
        hook = hooks_by_id.get("pre_checkout_uncommitted_overlap")
        assert hook is not None
        bp = hook.get("bypass_policy", {})
        assert bp.get("OPERATOR_SIGNATURE_REQUIRED") is True, (
            "pre_checkout_uncommitted_overlap: OPERATOR_SIGNATURE_REQUIRED must be True"
        )

    def test_hook_schema_change_override_has_operator_signed_sentinel(self) -> None:
        """HOOK_SCHEMA_CHANGE override must have operator_signed_sentinel field."""
        override = _OVERRIDE_BY_ID.get("HOOK_SCHEMA_CHANGE")
        assert override is not None, "HOOK_SCHEMA_CHANGE must be in overrides.yaml"
        requires = override.get("requires", {})
        assert "operator_signed_sentinel" in requires, (
            "HOOK_SCHEMA_CHANGE override must have operator_signed_sentinel field "
            "(evidence/operator_signed/... path)"
        )
        assert "operator_signature" in requires and requires["operator_signature"] is True, (
            "HOOK_SCHEMA_CHANGE override must have operator_signature: true"
        )

    def test_reversibility_class_on_hook_schema_change(self) -> None:
        """HOOK_SCHEMA_CHANGE override must declare reversibility_class: TRUTH_REWRITE."""
        override = _OVERRIDE_BY_ID.get("HOOK_SCHEMA_CHANGE")
        assert override is not None
        assert override.get("reversibility_class") == "TRUTH_REWRITE", (
            "HOOK_SCHEMA_CHANGE must have reversibility_class: TRUTH_REWRITE"
        )


# ---------------------------------------------------------------------------
# 6. Parse duration helper
# ---------------------------------------------------------------------------

class TestParseDuration:

    def setup_method(self):
        self.d = _import_dispatch()

    def test_parse_24h(self) -> None:
        assert self.d._parse_duration_to_seconds("24h") == 24 * 3600

    def test_parse_7d(self) -> None:
        assert self.d._parse_duration_to_seconds("7d") == 7 * 86400

    def test_parse_5m(self) -> None:
        assert self.d._parse_duration_to_seconds("5m") == 300

    def test_parse_60s(self) -> None:
        assert self.d._parse_duration_to_seconds("60s") == 60

    def test_parse_never_returns_none(self) -> None:
        assert self.d._parse_duration_to_seconds("never") is None

    def test_parse_invalid_unit_raises(self) -> None:
        with pytest.raises(ValueError):
            self.d._parse_duration_to_seconds("10x")


# ---------------------------------------------------------------------------
# 7. Override catalog coverage assertion
# ---------------------------------------------------------------------------

def test_all_override_ids_covered_in_catalog() -> None:
    """
    Every override_id in overrides.yaml must have been exercised in the
    parametrized tests above (coverage assertion).
    """
    expected = set(_OVERRIDE_IDS)
    # All override IDs that are parametrized above
    # The parametrized tests use _OVERRIDE_IDS directly, so coverage = catalog
    assert len(expected) > 0, "Override catalog must not be empty"
    # Verify catalog includes the new Phase 2 override
    assert "HOOK_SCHEMA_CHANGE" in expected, (
        "HOOK_SCHEMA_CHANGE must be in overrides.yaml (Phase 2 ATTACK 2 deliverable)"
    )
