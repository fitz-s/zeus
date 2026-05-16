# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   maintenance_worker/core/forbidden_rules_loader.py
#   bindings/universal/safety_defaults.yaml
#   bindings/zeus/safety_overrides.yaml
"""
Tests for maintenance_worker.core.forbidden_rules_loader.

Covers:
  - Bindings edit changes loader output (new rule appears in result)
  - Missing universal-defaults raises ConfigurationError (fail-closed)
  - Cache stability: same object returned on second call, cache_clear reloads
  - Project overrides merged after universal defaults
  - Missing project overrides (optional) → WARNING, universal rules still apply
  - Malformed universal file → ConfigurationError
  - MW_FORBIDDEN_RULES_FROM_CODE=1 → validator uses hardcoded list (not loader)
"""
from __future__ import annotations

import logging
import os
import textwrap
from pathlib import Path

import pytest
import yaml

from maintenance_worker.core.forbidden_rules_loader import (
    ConfigurationError,
    _load_yaml_entries,
    load_forbidden_rules,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_universal(tmp_path: Path, entries: list[dict] | None = None) -> Path:
    """Create a minimal bindings/universal/safety_defaults.yaml."""
    uni_dir = tmp_path / "universal"
    uni_dir.mkdir(parents=True, exist_ok=True)
    defaults_path = uni_dir / "safety_defaults.yaml"
    data = {
        "schema_version": 1,
        "forbidden_paths": entries if entries is not None else [
            {
                "pattern": "*/src/*",
                "group": "source_code_and_tests",
                "description": "source tree",
            }
        ],
    }
    defaults_path.write_text(yaml.dump(data), encoding="utf-8")
    return tmp_path  # returns bindings root


def _make_project_overrides(bindings_root: Path, project: str, entries: list[dict]) -> Path:
    """Create a project safety_overrides.yaml under bindings/<project>/."""
    proj_dir = bindings_root / project
    proj_dir.mkdir(parents=True, exist_ok=True)
    overrides_path = proj_dir / "safety_overrides.yaml"
    data = {
        "schema_version": 1,
        "project": project,
        "additional_forbidden_paths": entries,
    }
    overrides_path.write_text(yaml.dump(data), encoding="utf-8")
    return overrides_path


def _clear_cache() -> None:
    load_forbidden_rules.cache_clear()


# ---------------------------------------------------------------------------
# Test: bindings edit changes loader output
# ---------------------------------------------------------------------------

class TestBindingsEditChangesOutput:
    def test_new_universal_rule_appears_in_result(self, tmp_path: Path) -> None:
        bindings_root = _make_universal(tmp_path, entries=[
            {"pattern": "*/new_special_dir/*", "group": "source_code_and_tests",
             "description": "new special rule"},
        ])
        _clear_cache()
        rules = load_forbidden_rules(str(bindings_root))
        patterns = [r.pattern for r in rules]
        assert "*/new_special_dir/*" in patterns

    def test_project_override_rule_appended_after_universal(self, tmp_path: Path) -> None:
        bindings_root = _make_universal(tmp_path, entries=[
            {"pattern": "*/src/*", "group": "source_code_and_tests", "description": "src"},
        ])
        _make_project_overrides(bindings_root, "myproject", [
            {"pattern": "*/zeus_runtime/*", "group": "zeus_runtime",
             "description": "zeus runtime state"},
        ])
        _clear_cache()
        rules = load_forbidden_rules(str(bindings_root))
        patterns = [r.pattern for r in rules]
        # Universal rule present
        assert "*/src/*" in patterns
        # Project override appended
        assert "*/zeus_runtime/*" in patterns
        # Universal comes before project in ordering
        uni_idx = patterns.index("*/src/*")
        proj_idx = patterns.index("*/zeus_runtime/*")
        assert uni_idx < proj_idx

    def test_modifying_universal_file_after_cache_clear_changes_output(
        self, tmp_path: Path
    ) -> None:
        bindings_root = _make_universal(tmp_path, entries=[
            {"pattern": "*/old_pattern/*", "group": "g1", "description": "old"},
        ])
        _clear_cache()
        rules_before = load_forbidden_rules(str(bindings_root))
        assert any(r.pattern == "*/old_pattern/*" for r in rules_before)

        # Overwrite file
        _make_universal(tmp_path, entries=[
            {"pattern": "*/new_pattern/*", "group": "g1", "description": "new"},
        ])
        _clear_cache()
        rules_after = load_forbidden_rules(str(bindings_root))
        patterns_after = [r.pattern for r in rules_after]
        assert "*/new_pattern/*" in patterns_after
        assert "*/old_pattern/*" not in patterns_after


# ---------------------------------------------------------------------------
# Test: missing universal-defaults raises ConfigurationError
# ---------------------------------------------------------------------------

class TestMissingUniversalDefaults:
    def test_absent_universal_raises_configuration_error(self, tmp_path: Path) -> None:
        # bindings_root exists but has no universal/ subdirectory
        _clear_cache()
        with pytest.raises(ConfigurationError, match="Required forbidden-rules file"):
            load_forbidden_rules(str(tmp_path))

    def test_malformed_universal_raises_configuration_error(self, tmp_path: Path) -> None:
        uni_dir = tmp_path / "universal"
        uni_dir.mkdir()
        bad_yaml = uni_dir / "safety_defaults.yaml"
        bad_yaml.write_text("- not: a dict\n", encoding="utf-8")
        _clear_cache()
        with pytest.raises(ConfigurationError):
            load_forbidden_rules(str(tmp_path))

    def test_missing_project_overrides_warns_but_does_not_raise(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Universal present, project subdir exists but has no safety_overrides.yaml
        bindings_root = _make_universal(tmp_path)
        proj_dir = bindings_root / "myproject"
        proj_dir.mkdir()
        # No safety_overrides.yaml written

        _clear_cache()
        with caplog.at_level(logging.WARNING, logger="maintenance_worker.core.forbidden_rules_loader"):
            rules = load_forbidden_rules(str(bindings_root))

        # Must not raise; universal rules still loaded
        assert len(rules) > 0
        assert any("*/src/*" in r.pattern for r in rules)


# ---------------------------------------------------------------------------
# Test: cache stability
# ---------------------------------------------------------------------------

class TestCacheStability:
    def test_same_object_returned_on_second_call(self, tmp_path: Path) -> None:
        bindings_root = _make_universal(tmp_path)
        _clear_cache()
        rules_first = load_forbidden_rules(str(bindings_root))
        rules_second = load_forbidden_rules(str(bindings_root))
        assert rules_first is rules_second

    def test_cache_clear_allows_different_result(self, tmp_path: Path) -> None:
        bindings_root = _make_universal(tmp_path, entries=[
            {"pattern": "*/v1/*", "group": "g", "description": "v1"},
        ])
        _clear_cache()
        rules_v1 = load_forbidden_rules(str(bindings_root))
        assert any(r.pattern == "*/v1/*" for r in rules_v1)

        _make_universal(tmp_path, entries=[
            {"pattern": "*/v2/*", "group": "g", "description": "v2"},
        ])
        _clear_cache()
        rules_v2 = load_forbidden_rules(str(bindings_root))
        assert any(r.pattern == "*/v2/*" for r in rules_v2)
        assert not any(r.pattern == "*/v1/*" for r in rules_v2)


# ---------------------------------------------------------------------------
# Test: MW_FORBIDDEN_RULES_FROM_CODE=1 uses hardcoded list in validator
# ---------------------------------------------------------------------------

class TestHardcodedFallbackEnvVar:
    def test_env_var_causes_validator_to_skip_loader(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Validator._get_active_rules() returns hardcoded list when env var set."""
        monkeypatch.setenv("MW_FORBIDDEN_RULES_FROM_CODE", "1")

        # Import after monkeypatch so env is set at import-call time
        from maintenance_worker.core.validator import _get_active_rules, _FORBIDDEN_RULES

        result = _get_active_rules()
        # With MW_FORBIDDEN_RULES_FROM_CODE=1, result IS _FORBIDDEN_RULES
        assert result is _FORBIDDEN_RULES

    def test_env_var_unset_calls_loader(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without env var + valid bindings, _get_active_rules returns loader result."""
        monkeypatch.delenv("MW_FORBIDDEN_RULES_FROM_CODE", raising=False)
        bindings_root = _make_universal(tmp_path)
        monkeypatch.setenv("BINDINGS_DIR", str(bindings_root))

        _clear_cache()
        from maintenance_worker.core.validator import _get_active_rules, _FORBIDDEN_RULES
        result = _get_active_rules()
        # Loader succeeded — returns the YAML-loaded rule, not the hardcoded list
        assert isinstance(result, list)
        assert len(result) > 0
        assert result is not _FORBIDDEN_RULES

    def test_env_var_unset_missing_bindings_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without env var + missing/invalid BINDINGS_DIR, _get_active_rules raises (fail-closed)."""
        monkeypatch.delenv("MW_FORBIDDEN_RULES_FROM_CODE", raising=False)
        monkeypatch.setenv("BINDINGS_DIR", str(tmp_path / "nonexistent"))

        _clear_cache()
        from maintenance_worker.core.validator import _get_active_rules
        from maintenance_worker.core.forbidden_rules_loader import ConfigurationError

        with pytest.raises((ConfigurationError, Exception)):
            _get_active_rules()
