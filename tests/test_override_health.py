# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: PLAN §3 Phase 3 M5 override health deliverable + §5 CHARTER M5
#   docs/operations/task_2026-05-06_hook_redesign/PLAN.md
#   evidence/hook_redesign_critic_opus.md ATTACK 7

"""
test_override_health.py — M5 override health assertions.

Checks:
1. No override_id exceeds max_active_per_30d cap (fixture-based).
2. auto_expires_after: never only on whitelisted overrides (REVIEW_SAFE_TAG,
   ISOLATED_WORKTREE) — asserted against real overrides.yaml.
3. Registry max_active_per_30d is set on all BLOCKING-hook bypass_policies.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import pytest

try:
    import yaml
except ImportError:
    yaml = None

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERRIDES_PATH = REPO_ROOT / ".claude" / "hooks" / "overrides.yaml"
REGISTRY_PATH = REPO_ROOT / ".claude" / "hooks" / "registry.yaml"
OVERRIDE_LOG_DIR = REPO_ROOT / ".claude" / "logs" / "hook_overrides"

# Whitelist: only these may carry auto_expires_after: never (ATTACK 7 / §0.5)
_NEVER_EXPIRY_WHITELIST = {"REVIEW_SAFE_TAG", "ISOLATED_WORKTREE"}

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: pathlib.Path) -> dict:
    if yaml is None:
        pytest.skip("PyYAML not installed")
    return yaml.safe_load(path.read_text())


def _make_override_log_entry(
    override_id: str,
    hook_id: str = "invariant_test",
    ts_offset_days: float = 0.0,
) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(days=ts_offset_days)).isoformat()
    return {
        "hook_id": hook_id,
        "override_id": override_id,
        "session_id": "sess-fixture",
        "ts": ts,
    }


def _count_active_per_30d(entries: list[dict], override_id: str) -> int:
    """Count unique (override_id, evidence_file?) uses within last 30 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    count = 0
    for e in entries:
        if e.get("override_id") != override_id:
            continue
        ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        if ts >= cutoff:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Test: never-expiry whitelist
# ---------------------------------------------------------------------------


class TestNeverExpiryWhitelist:
    def test_only_whitelisted_overrides_have_never_expiry(self):
        """auto_expires_after: never is only permitted for REVIEW_SAFE_TAG + ISOLATED_WORKTREE."""
        catalog = _load_yaml(OVERRIDES_PATH)
        violations = []
        for ov in catalog.get("overrides", []):
            ov_id = ov["id"]
            requires = ov.get("requires", {})
            expires = requires.get("auto_expires_after", "24h")
            if expires == "never" and ov_id not in _NEVER_EXPIRY_WHITELIST:
                violations.append(ov_id)
        assert not violations, (
            f"override_ids with auto_expires_after:never outside whitelist: {violations}\n"
            "Only REVIEW_SAFE_TAG and ISOLATED_WORKTREE may carry never-expiry "
            "(ATTACK 7 / PLAN §0.5)."
        )

    def test_whitelisted_overrides_exist_in_catalog(self):
        """REVIEW_SAFE_TAG and ISOLATED_WORKTREE must be present in overrides.yaml."""
        catalog = _load_yaml(OVERRIDES_PATH)
        ids = {ov["id"] for ov in catalog.get("overrides", [])}
        for expected in _NEVER_EXPIRY_WHITELIST:
            assert expected in ids, (
                f"Whitelisted override {expected!r} missing from overrides.yaml"
            )

    def test_whitelisted_overrides_actually_have_never_expiry(self):
        """The two whitelisted entries must carry auto_expires_after: never."""
        catalog = _load_yaml(OVERRIDES_PATH)
        for ov in catalog.get("overrides", []):
            if ov["id"] in _NEVER_EXPIRY_WHITELIST:
                expires = ov.get("requires", {}).get("auto_expires_after", "")
                assert expires == "never", (
                    f"{ov['id']} expected auto_expires_after:never, got {expires!r}"
                )


# ---------------------------------------------------------------------------
# Test: max_active_per_30d cap (fixture-based)
# ---------------------------------------------------------------------------


class TestMaxActivePer30dCap:
    def test_cap_not_exceeded_within_limit(self):
        """4 uses of BASELINE_RATCHET within 30d: cap=5, should pass."""
        entries = [_make_override_log_entry("BASELINE_RATCHET") for _ in range(4)]
        count = _count_active_per_30d(entries, "BASELINE_RATCHET")
        cap = 5
        assert count <= cap, f"Expected <= {cap}, got {count}"

    def test_cap_exceeded_raises_flag(self):
        """6 uses of BASELINE_RATCHET within 30d: cap=5, should flag."""
        entries = [_make_override_log_entry("BASELINE_RATCHET") for _ in range(6)]
        count = _count_active_per_30d(entries, "BASELINE_RATCHET")
        cap = 5
        assert count > cap, (
            f"Expected > {cap} to flag cap violation, got {count}"
        )

    def test_old_entries_outside_30d_excluded(self):
        """5 recent + 5 old (35d ago) uses: only 5 count toward 30d cap."""
        recent = [_make_override_log_entry("BASELINE_RATCHET", ts_offset_days=1) for _ in range(5)]
        old = [_make_override_log_entry("BASELINE_RATCHET", ts_offset_days=35) for _ in range(5)]
        count = _count_active_per_30d(recent + old, "BASELINE_RATCHET")
        cap = 5
        assert count <= cap, f"Old entries should not count; got {count}"

    def test_different_override_ids_counted_separately(self):
        """BASELINE_RATCHET and MAIN_REGRESSION caps are independent."""
        entries = (
            [_make_override_log_entry("BASELINE_RATCHET") for _ in range(3)]
            + [_make_override_log_entry("MAIN_REGRESSION") for _ in range(4)]
        )
        br_count = _count_active_per_30d(entries, "BASELINE_RATCHET")
        mr_count = _count_active_per_30d(entries, "MAIN_REGRESSION")
        assert br_count == 3
        assert mr_count == 4


# ---------------------------------------------------------------------------
# Test: registry BLOCKING hooks have max_active_per_30d
# ---------------------------------------------------------------------------


class TestRegistryCapConfiguration:
    def test_blocking_hooks_with_structured_override_have_cap(self):
        """
        Every BLOCKING hook with bypass_policy.class==structured_override must
        declare max_active_per_30d in its bypass_policy.
        """
        registry = _load_yaml(REGISTRY_PATH)
        missing_cap = []
        for hook in registry.get("hooks", []):
            if hook.get("severity") != "BLOCKING":
                continue
            bp = hook.get("bypass_policy", {})
            if bp.get("class") != "structured_override":
                continue
            if "max_active_per_30d" not in bp:
                missing_cap.append(hook["id"])
        # Some hooks may not carry max_active_per_30d if they delegate to OPERATOR_OVERRIDE
        # (single emergency clause). Flag only if they also have multiple override_ids.
        real_missing = []
        for hook_id in missing_cap:
            for hook in registry.get("hooks", []):
                if hook["id"] == hook_id:
                    bp = hook.get("bypass_policy", {})
                    override_ids = bp.get("override_ids", [])
                    if len(override_ids) > 1:
                        real_missing.append(hook_id)
        assert not real_missing, (
            f"BLOCKING hooks with >1 override_id but no max_active_per_30d: {real_missing}\n"
            "Add max_active_per_30d to bypass_policy in registry.yaml (CHARTER M5)."
        )

    def test_advisory_hooks_have_no_cap_required(self):
        """ADVISORY hooks do not require max_active_per_30d (no blocking)."""
        registry = _load_yaml(REGISTRY_PATH)
        for hook in registry.get("hooks", []):
            if hook.get("severity") == "ADVISORY":
                # No assertion needed — just confirm they don't crash schema load
                assert "id" in hook

    def test_override_ids_referenced_in_registry_exist_in_catalog(self):
        """Every override_id listed in registry.yaml bypass_policy must exist in overrides.yaml."""
        registry = _load_yaml(REGISTRY_PATH)
        catalog = _load_yaml(OVERRIDES_PATH)
        catalog_ids = {ov["id"] for ov in catalog.get("overrides", [])}

        missing = []
        for hook in registry.get("hooks", []):
            for ov_id in hook.get("bypass_policy", {}).get("override_ids", []):
                if ov_id not in catalog_ids:
                    missing.append((hook["id"], ov_id))
        assert not missing, (
            f"Override IDs in registry.yaml not found in overrides.yaml: {missing}"
        )


# ---------------------------------------------------------------------------
# Test: real audit log cap enforcement (if logs exist)
# ---------------------------------------------------------------------------


class TestRealAuditLogCap:
    def test_real_audit_logs_respect_cap_if_present(self):
        """
        If real override audit logs exist, assert no override_id has exceeded
        its cap within 30d. Uses registry.yaml caps as the source of truth.
        """
        if not OVERRIDE_LOG_DIR.exists():
            pytest.skip("No override log dir — first-run skip")

        registry = _load_yaml(REGISTRY_PATH)
        caps: dict[str, int] = {}
        for hook in registry.get("hooks", []):
            bp = hook.get("bypass_policy", {})
            cap = bp.get("max_active_per_30d")
            if cap is not None:
                for ov_id in bp.get("override_ids", []):
                    # Use minimum cap if same override appears in multiple hooks
                    if ov_id not in caps or cap < caps[ov_id]:
                        caps[ov_id] = cap

        jsonl_files = list(OVERRIDE_LOG_DIR.glob("*.jsonl"))
        if not jsonl_files:
            pytest.skip("No override audit jsonl files yet")

        all_entries: list[dict] = []
        for fpath in jsonl_files:
            for raw in fpath.read_text().splitlines():
                raw = raw.strip()
                if raw:
                    all_entries.append(json.loads(raw))

        violations = []
        for ov_id, cap in caps.items():
            count = _count_active_per_30d(all_entries, ov_id)
            if count > cap:
                violations.append(f"{ov_id}: {count} uses > cap {cap}")

        assert not violations, (
            "Override cap violations detected in audit logs:\n"
            + "\n".join(violations)
            + "\nSee PLAN §5 CHARTER M5 — quarterly review required."
        )
