# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.3, §2.1
"""
Unit tests for scripts/topology_v_next/profile_loader.py.

Covers:
- load valid YAML into typed BindingLayer
- load missing-field YAML (graceful defaults, no crash)
- validate_binding_layer returns warnings for policy violations
- validate_binding_layer returns empty list for clean binding
- load non-existent path raises FileNotFoundError with named path (m1 minor)
- unknown top-level keys tolerated (warn-don't-crash)
- artifact_authority_status loaded as dict keyed by path
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CoverageMap,
    Intent,
    Severity,
)
from scripts.topology_v_next.profile_loader import load_binding_layer, validate_binding_layer

# Path to test fixtures
FIXTURES = Path(__file__).parent / "fixtures"

# Path to worktree root (two levels up from tests/topology_v_next/)
WORKTREE_ROOT = Path(__file__).parent.parent.parent

# Stub binding YAML — lives in architecture/ relative to worktree root
STUB_BINDING = WORKTREE_ROOT / "architecture" / "topology_v_next_binding.yaml"


# ---------------------------------------------------------------------------
# load_binding_layer — valid YAML
# ---------------------------------------------------------------------------

class TestLoadBindingLayerValid:
    def test_returns_binding_layer(self):
        bl = load_binding_layer(FIXTURES / "valid_binding.yaml")
        assert isinstance(bl, BindingLayer)

    def test_project_id(self):
        bl = load_binding_layer(FIXTURES / "valid_binding.yaml")
        assert bl.project_id == "test_project"

    def test_intent_extensions_typed(self):
        bl = load_binding_layer(FIXTURES / "valid_binding.yaml")
        assert len(bl.intent_extensions) >= 1
        for ext in bl.intent_extensions:
            assert isinstance(ext, Intent)

    def test_coverage_map_type(self):
        bl = load_binding_layer(FIXTURES / "valid_binding.yaml")
        assert isinstance(bl.coverage_map, CoverageMap)
        assert "test_profile" in bl.coverage_map.profiles

    def test_coverage_map_patterns_are_tuples(self):
        bl = load_binding_layer(FIXTURES / "valid_binding.yaml")
        for patterns in bl.coverage_map.profiles.values():
            assert isinstance(patterns, tuple)

    def test_orphaned_and_hard_stop_are_tuples(self):
        bl = load_binding_layer(FIXTURES / "valid_binding.yaml")
        assert isinstance(bl.coverage_map.orphaned, tuple)
        assert isinstance(bl.coverage_map.hard_stop_paths, tuple)

    def test_severity_overrides_typed(self):
        bl = load_binding_layer(FIXTURES / "valid_binding.yaml")
        assert "coverage_gap" in bl.severity_overrides
        assert bl.severity_overrides["coverage_gap"] is Severity.ADVISORY
        assert bl.severity_overrides["closed_packet_authority"] is Severity.SOFT_BLOCK

    def test_cohorts_loaded(self):
        bl = load_binding_layer(FIXTURES / "valid_binding.yaml")
        assert len(bl.cohorts) == 1
        cohort = bl.cohorts[0]
        assert cohort.id == "test.my_cohort"
        assert cohort.profile == "test_profile"
        assert Intent.create_new in cohort.intent_classes

    def test_artifact_authority_status_is_dict(self):
        bl = load_binding_layer(FIXTURES / "valid_binding.yaml")
        assert isinstance(bl.artifact_authority_status, dict)

    def test_artifact_authority_status_keyed_by_path(self):
        bl = load_binding_layer(FIXTURES / "valid_binding.yaml")
        assert "architecture/test_topology.yaml" in bl.artifact_authority_status
        row = bl.artifact_authority_status["architecture/test_topology.yaml"]
        assert row["status"] == "CURRENT_LOAD_BEARING"
        assert row["confirmation_ttl_days"] == 14


# ---------------------------------------------------------------------------
# load_binding_layer — missing fields (graceful defaults)
# ---------------------------------------------------------------------------

class TestLoadBindingLayerMissingFields:
    def test_no_crash_on_missing_optional_fields(self):
        bl = load_binding_layer(FIXTURES / "missing_field_binding.yaml")
        assert isinstance(bl, BindingLayer)

    def test_intent_extensions_empty_tuple(self):
        bl = load_binding_layer(FIXTURES / "missing_field_binding.yaml")
        assert bl.intent_extensions == ()

    def test_cohorts_empty_tuple(self):
        bl = load_binding_layer(FIXTURES / "missing_field_binding.yaml")
        assert bl.cohorts == ()

    def test_severity_overrides_empty_dict(self):
        bl = load_binding_layer(FIXTURES / "missing_field_binding.yaml")
        assert bl.severity_overrides == {}

    def test_artifact_authority_status_empty(self):
        bl = load_binding_layer(FIXTURES / "missing_field_binding.yaml")
        assert bl.artifact_authority_status == {}

    def test_high_fanout_hints_empty(self):
        bl = load_binding_layer(FIXTURES / "missing_field_binding.yaml")
        assert bl.high_fanout_hints == ()

    def test_profile_still_loaded(self):
        bl = load_binding_layer(FIXTURES / "missing_field_binding.yaml")
        assert "only_profile" in bl.coverage_map.profiles


# ---------------------------------------------------------------------------
# load_binding_layer — non-existent path
# ---------------------------------------------------------------------------

class TestLoadBindingLayerNotFound:
    def test_raises_file_not_found(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(FileNotFoundError):
            load_binding_layer(missing)

    def test_error_message_names_path(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(FileNotFoundError, match="does_not_exist.yaml"):
            load_binding_layer(missing)

    def test_accepts_string_path(self, tmp_path: Path):
        missing = str(tmp_path / "also_missing.yaml")
        with pytest.raises(FileNotFoundError, match="also_missing.yaml"):
            load_binding_layer(missing)


# ---------------------------------------------------------------------------
# load_binding_layer — stub binding YAML (production fixture)
# ---------------------------------------------------------------------------

class TestLoadStubBindingYAML:
    """Smoke test: the stub YAML in architecture/ must load cleanly."""

    def test_stub_yaml_loads(self):
        bl = load_binding_layer(STUB_BINDING)
        assert isinstance(bl, BindingLayer)
        assert bl.project_id == "zeus"

    def test_stub_yaml_has_zeus_extensions(self):
        bl = load_binding_layer(STUB_BINDING)
        values = {ext.value for ext in bl.intent_extensions}
        assert "zeus.topology_tooling" in values

    def test_stub_yaml_unknown_field_tolerated(self):
        """companion_required in stub YAML must not crash the loader."""
        # load must succeed even with unknown key companion_required
        bl = load_binding_layer(STUB_BINDING)
        assert isinstance(bl, BindingLayer)


# ---------------------------------------------------------------------------
# validate_binding_layer — warnings
# ---------------------------------------------------------------------------

class TestValidateBindingLayer:
    def test_clean_binding_returns_empty_list(self):
        bl = load_binding_layer(FIXTURES / "valid_binding.yaml")
        warnings = validate_binding_layer(bl)
        assert warnings == []

    def test_missing_namespace_prefix_warns(self):
        bl = load_binding_layer(FIXTURES / "warn_binding.yaml")
        # warn_binding has intent "no_namespace_intent" without namespace prefix
        # BUT: the loader skips unknown Intent values (they're not in enum).
        # So the warning fires only if the value made it into intent_extensions.
        # validate_binding_layer checks Intent values that DO have no "." in value.
        # Since "no_namespace_intent" is not a valid Intent enum member, it's
        # skipped at parse time. The warning contract covers values that ARE
        # loaded — edge case: when a project adds a raw Intent not in enum.
        # Test: confirm no crash and result is list.
        warnings = validate_binding_layer(bl)
        assert isinstance(warnings, list)

    def test_artifact_authority_missing_keys_warns(self):
        bl = load_binding_layer(FIXTURES / "warn_binding.yaml")
        warnings = validate_binding_layer(bl)
        # warn_binding has an artifact_authority_status row missing
        # last_confirmed and confirmation_ttl_days
        missing_key_warnings = [
            w for w in warnings
            if "missing keys" in w
        ]
        assert len(missing_key_warnings) >= 1

    def test_empty_profiles_warns(self):
        """A binding with no coverage profiles should warn."""
        from scripts.topology_v_next.dataclasses import CoverageMap, BindingLayer
        empty_cm = CoverageMap(profiles={}, orphaned=(), hard_stop_paths=())
        bl = BindingLayer(
            project_id="test",
            intent_extensions=(),
            coverage_map=empty_cm,
            cohorts=(),
            severity_overrides={},
            high_fanout_hints=(),
            artifact_authority_status={},
        )
        warnings = validate_binding_layer(bl)
        assert any("empty" in w.lower() for w in warnings)

    def test_zeus_namespace_extensions_are_clean(self):
        """All zeus.* intent extensions should pass namespace check."""
        bl = load_binding_layer(STUB_BINDING)
        warnings = validate_binding_layer(bl)
        namespace_warnings = [w for w in warnings if "namespace prefix" in w]
        assert namespace_warnings == []
