# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   maintenance_worker/core/validator.py _FORBIDDEN_RULES (hardcoded reference)
#   bindings/universal/safety_defaults.yaml + bindings/zeus/safety_overrides.yaml
"""
Regression test: YAML-loaded rules must be a superset of the hardcoded _FORBIDDEN_RULES.

Ensures that every hardcoded rule in _FORBIDDEN_RULES has an equivalent in the shipped
YAML bindings. Catches cases where YAML and hardcoded lists diverge silently (M2 gap).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from maintenance_worker.core.validator import _FORBIDDEN_RULES
from maintenance_worker.core.forbidden_rules_loader import load_forbidden_rules


# Path to the actual shipped bindings directory
# test lives at tests/maintenance_worker/test_yaml_rule_coverage.py
# parents[0]=tests/maintenance_worker, parents[1]=tests, parents[2]=repo_root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BINDINGS_DIR = _REPO_ROOT / "bindings"


class TestYamlRuleCoverage:
    def setup_method(self) -> None:
        # Clear cache so the real bindings are loaded fresh each run
        load_forbidden_rules.cache_clear()

    def teardown_method(self) -> None:
        load_forbidden_rules.cache_clear()

    def test_yaml_rules_count_ge_hardcoded(self) -> None:
        """Loaded YAML rules must have at least as many entries as _FORBIDDEN_RULES."""
        yaml_rules = load_forbidden_rules(str(_BINDINGS_DIR))
        assert len(yaml_rules) >= len(_FORBIDDEN_RULES), (
            f"YAML loaded {len(yaml_rules)} rules but hardcoded list has "
            f"{len(_FORBIDDEN_RULES)}. Missing rules will silently degrade protection."
        )

    def test_each_hardcoded_rule_has_yaml_equivalent(self) -> None:
        """Every hardcoded ForbiddenRule must appear in the YAML-loaded set."""
        yaml_rules = load_forbidden_rules(str(_BINDINGS_DIR))

        # Build lookup key: (pattern, group, prefix, exact_name)
        yaml_keys = {
            (r.pattern, r.group, r.prefix, r.exact_name)
            for r in yaml_rules
        }

        missing = []
        for rule in _FORBIDDEN_RULES:
            key = (rule.pattern, rule.group, rule.prefix, rule.exact_name)
            if key not in yaml_keys:
                missing.append(
                    f"  pattern={rule.pattern!r}, group={rule.group!r}, "
                    f"prefix={rule.prefix}, exact_name={rule.exact_name}"
                )

        assert not missing, (
            f"{len(missing)} hardcoded rule(s) have no YAML equivalent:\n"
            + "\n".join(missing)
        )
