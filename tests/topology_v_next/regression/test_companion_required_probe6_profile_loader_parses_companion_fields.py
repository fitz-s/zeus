# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md §2.1, §2.2
"""
probe6 — profile_loader correctly parses companion_required and
         companion_skip_acknowledge_token from per-profile YAML entries.

Verifies the profile_loader round-trip: YAML with companion_required and
companion_skip_acknowledge_token per profile → BindingLayer.companion_required
and BindingLayer.companion_skip_tokens populated correctly.

Also verifies validate_binding_layer emits companion_token_malformed advisory
for malformed tokens and companion_target_missing advisory for missing paths.
"""
from __future__ import annotations

import yaml
from pathlib import Path

import pytest

from scripts.topology_v_next.profile_loader import load_binding_layer, validate_binding_layer


_MINIMAL_YAML_WITH_COMPANION = """\
project_id: test_proj
coverage_map:
  profiles:
    - id: test_profile
      patterns:
        - "src/test/*.py"
      companion_required:
        - "docs/reference/test_authority.md"
      companion_skip_acknowledge_token: "MY_SKIP_TOKEN=ok"
  orphaned: []
  hard_stop_paths: []
cohorts: []
severity_overrides: {}
artifact_authority_status: {}
"""

_YAML_WITH_MALFORMED_TOKEN = """\
project_id: test_proj
coverage_map:
  profiles:
    - id: test_profile
      patterns:
        - "src/test/*.py"
      companion_required:
        - "docs/reference/test_authority.md"
      companion_skip_acknowledge_token: "bad token with spaces"
  orphaned: []
  hard_stop_paths: []
cohorts: []
severity_overrides: {}
artifact_authority_status: {}
"""


class TestProbe6ProfileLoaderParsesCompanionFields:
    def test_companion_required_parsed(self, tmp_path: Path):
        binding_file = tmp_path / "binding.yaml"
        binding_file.write_text(_MINIMAL_YAML_WITH_COMPANION)

        bl = load_binding_layer(binding_file)

        assert "test_profile" in bl.companion_required
        assert "docs/reference/test_authority.md" in bl.companion_required["test_profile"]

    def test_companion_skip_token_parsed(self, tmp_path: Path):
        binding_file = tmp_path / "binding.yaml"
        binding_file.write_text(_MINIMAL_YAML_WITH_COMPANION)

        bl = load_binding_layer(binding_file)

        assert bl.companion_skip_tokens.get("test_profile") == "MY_SKIP_TOKEN=ok"

    def test_profile_without_companion_has_empty_entry(self, tmp_path: Path):
        """Profiles without companion_required have no entry in companion_required."""
        yaml_content = """\
project_id: test_proj
coverage_map:
  profiles:
    - id: plain_profile
      patterns:
        - "src/plain/*.py"
  orphaned: []
  hard_stop_paths: []
cohorts: []
severity_overrides: {}
artifact_authority_status: {}
"""
        binding_file = tmp_path / "binding.yaml"
        binding_file.write_text(yaml_content)

        bl = load_binding_layer(binding_file)

        assert "plain_profile" not in bl.companion_required
        assert "plain_profile" not in bl.companion_skip_tokens

    def test_malformed_token_emits_validation_warning(self, tmp_path: Path):
        binding_file = tmp_path / "binding.yaml"
        binding_file.write_text(_YAML_WITH_MALFORMED_TOKEN)

        bl = load_binding_layer(binding_file)
        warnings = validate_binding_layer(bl)

        assert any("companion_token_malformed" in w for w in warnings), (
            f"Expected companion_token_malformed warning; got: {warnings}"
        )

    def test_missing_companion_path_emits_validation_warning(self, tmp_path: Path):
        """Path that doesn't exist on disk emits companion_target_missing advisory."""
        binding_file = tmp_path / "binding.yaml"
        binding_file.write_text(_MINIMAL_YAML_WITH_COMPANION)

        bl = load_binding_layer(binding_file)
        warnings = validate_binding_layer(bl)

        # docs/reference/test_authority.md does not exist on disk
        assert any("companion_target_missing" in w for w in warnings), (
            f"Expected companion_target_missing warning; got: {warnings}"
        )

    def test_p1_binding_loads_with_no_companion_fields(self, tmp_path: Path):
        """Existing P1 YAML (no companion_required) loads with empty dicts."""
        p1_yaml = """\
project_id: zeus
coverage_map:
  profiles:
    - id: agent_runtime
      patterns:
        - "scripts/topology_doctor.py"
  orphaned: []
  hard_stop_paths: []
cohorts: []
severity_overrides: {}
artifact_authority_status: {}
"""
        binding_file = tmp_path / "binding.yaml"
        binding_file.write_text(p1_yaml)

        bl = load_binding_layer(binding_file)

        assert bl.companion_required == {}
        assert bl.companion_skip_tokens == {}
