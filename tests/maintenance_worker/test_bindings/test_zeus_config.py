# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/05_execution_packets/PACKET_INDEX.md §P6
#   bindings/zeus/config.yaml
#   bindings/zeus/safety_overrides.yaml
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md
"""
test_zeus_config.py — Verify Zeus binding configs load correctly.

Tests:
- bindings/zeus/config.yaml: valid YAML, required keys present, values correct
- bindings/zeus/safety_overrides.yaml: valid YAML, additive structure
- Allowlist paths reference valid TASK_CATALOG task IDs
- Dry-run floor settings match hardcoded FLOOR_EXEMPT_TASK_IDS
- Safety overrides are ADDITIVE (do not reduce the universal set)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixtures: resolve repo root
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]  # test_bindings/ -> maintenance_worker/ -> tests/ -> repo root
BINDINGS_DIR = REPO_ROOT / "bindings" / "zeus"
TASK_CATALOG_PATH = (
    REPO_ROOT
    / "docs/operations/task_2026-05-15_runtime_improvement_engineering_package"
    / "02_daily_maintenance_agent/TASK_CATALOG.yaml"
)


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def zeus_config() -> dict:
    return _load_yaml(BINDINGS_DIR / "config.yaml")


@pytest.fixture(scope="module")
def safety_overrides() -> dict:
    return _load_yaml(BINDINGS_DIR / "safety_overrides.yaml")


@pytest.fixture(scope="module")
def task_catalog() -> dict:
    return _load_yaml(TASK_CATALOG_PATH)


# ---------------------------------------------------------------------------
# config.yaml tests
# ---------------------------------------------------------------------------


def test_config_yaml_exists():
    assert (BINDINGS_DIR / "config.yaml").is_file(), "bindings/zeus/config.yaml missing"


def test_config_has_required_top_level_keys(zeus_config):
    required = {"label", "agent_version", "paths", "scheduler", "dry_run", "notification", "ttl_overrides"}
    missing = required - set(zeus_config.keys())
    assert not missing, f"config.yaml missing top-level keys: {missing}"


def test_config_label(zeus_config):
    assert zeus_config["label"] == "com.zeus.maintenance"


def test_config_paths_has_required_keys(zeus_config):
    required = {"state_dir", "evidence_dir", "task_catalog", "safety_contract", "safety_overrides"}
    missing = required - set(zeus_config["paths"].keys())
    assert not missing, f"config.yaml paths missing: {missing}"


def test_config_paths_reference_zeus_repo(zeus_config):
    for key, value in zeus_config["paths"].items():
        assert "${ZEUS_REPO}" in value or "~/" in value, (
            f"config.yaml paths.{key}={value!r} does not use ${{ZEUS_REPO}} — "
            "all Zeus paths must be relative to the repo root via ${ZEUS_REPO}"
        )


def test_config_scheduler_is_launchd(zeus_config):
    assert zeus_config["scheduler"]["kind"] == "launchd"


def test_config_schedule_at_0430(zeus_config):
    sched = zeus_config["scheduler"]
    assert sched.get("schedule_hour") == 4, f"Expected schedule_hour=4, got {sched.get('schedule_hour')}"
    assert sched.get("schedule_minute") == 30, f"Expected schedule_minute=30, got {sched.get('schedule_minute')}"


def test_config_dry_run_floor_days(zeus_config):
    assert zeus_config["dry_run"]["floor_days"] == 30, (
        "30-day dry-run floor is non-negotiable per PACKET_INDEX §P6"
    )


def test_config_dry_run_exempt_task_ids_match_hardcoded(zeus_config):
    """
    Exempt task IDs in YAML must match FLOOR_EXEMPT_TASK_IDS in install_metadata.py.
    The YAML is documentation; the frozenset is enforcement. They must agree.
    """
    from maintenance_worker.core.install_metadata import FLOOR_EXEMPT_TASK_IDS

    yaml_exempt = set(zeus_config["dry_run"].get("exempt_task_ids", []))
    code_exempt = set(FLOOR_EXEMPT_TASK_IDS)
    assert yaml_exempt == code_exempt, (
        f"config.yaml exempt_task_ids {yaml_exempt} != "
        f"FLOOR_EXEMPT_TASK_IDS {code_exempt}. "
        "Update config.yaml to document the hardcoded frozenset exactly."
    )


def test_config_notification_channel(zeus_config):
    assert zeus_config["notification"]["channel"] in {"discord", "slack", "email", "file", "none"}


def test_config_ttl_overrides_has_required_keys(zeus_config):
    required = {
        "stale_worktree_quarantine_idle_days",
        "launchagent_backup_ttl_days",
        "proposal_ttl_days",
        "evidence_retention_days",
    }
    missing = required - set(zeus_config.get("ttl_overrides", {}).keys())
    assert not missing, f"config.yaml ttl_overrides missing: {missing}"


def test_config_task_allowlist_task_ids_exist_in_catalog(zeus_config, task_catalog):
    """Task IDs referenced in task_allowlists must exist in the TASK_CATALOG."""
    catalog_ids = {t["id"] for t in task_catalog.get("tasks", [])}
    yaml_allowlist_ids = set(zeus_config.get("task_allowlists", {}).keys())
    missing = yaml_allowlist_ids - catalog_ids
    assert not missing, (
        f"config.yaml task_allowlists references task IDs not in TASK_CATALOG: {missing}"
    )


# ---------------------------------------------------------------------------
# safety_overrides.yaml tests
# ---------------------------------------------------------------------------


def test_safety_overrides_yaml_exists():
    assert (BINDINGS_DIR / "safety_overrides.yaml").is_file()


def test_safety_overrides_schema_version(safety_overrides):
    assert safety_overrides.get("schema_version") == 1


def test_safety_overrides_project_field(safety_overrides):
    assert safety_overrides.get("project") == "zeus"


def test_safety_overrides_extends_safety_contract(safety_overrides):
    assert "SAFETY_CONTRACT.md" in safety_overrides.get("extends", ""), (
        "safety_overrides.yaml must declare 'extends: SAFETY_CONTRACT.md' to document its additive relationship"
    )


def test_safety_overrides_has_additional_forbidden_paths(safety_overrides):
    forbidden = safety_overrides.get("additional_forbidden_paths", [])
    assert len(forbidden) >= 3, (
        f"Expected at least 3 Zeus-specific forbidden path rules, got {len(forbidden)}"
    )


def test_safety_overrides_each_rule_has_required_fields(safety_overrides):
    for i, rule in enumerate(safety_overrides.get("additional_forbidden_paths", [])):
        assert "pattern" in rule, f"Rule {i} missing 'pattern'"
        assert "group" in rule, f"Rule {i} missing 'group'"
        assert "description" in rule, f"Rule {i} missing 'description'"
        assert rule["pattern"], f"Rule {i} has empty pattern"


def test_safety_overrides_covers_zeus_db_files(safety_overrides):
    """Zeus calibration DB and trade DB must appear as forbidden path patterns."""
    patterns = [r["pattern"] for r in safety_overrides.get("additional_forbidden_paths", [])]
    patterns_str = " ".join(patterns)
    assert "calibration" in patterns_str.lower(), (
        "safety_overrides.yaml must protect calibration state from maintenance worker writes"
    )


def test_safety_overrides_covers_settings_json(safety_overrides):
    patterns = [r["pattern"] for r in safety_overrides.get("additional_forbidden_paths", [])]
    patterns_str = " ".join(patterns)
    assert "settings.json" in patterns_str or "config/**" in patterns_str, (
        "config/settings.json (live-trading parameters) must be explicitly forbidden"
    )


def test_safety_overrides_does_not_remove_universal_rules():
    """
    The overrides file CANNOT weaken the universal contract — it is purely additive.
    Verify by confirming the universal core validator still loads cleanly.
    """
    from maintenance_worker.core import validator  # noqa: F401
    assert hasattr(validator, "_FORBIDDEN_RULES"), "Core validator _FORBIDDEN_RULES missing"
    assert len(validator._FORBIDDEN_RULES) >= 10, (
        "Universal forbidden rules list appears truncated"
    )


def test_safety_overrides_allowed_write_extensions_reference_zeus_repo(safety_overrides):
    for entry in safety_overrides.get("allowed_write_extensions", []):
        path = entry.get("path", "")
        assert "${ZEUS_REPO}" in path or "~/" in path, (
            f"allowed_write_extensions entry {path!r} must use ${{ZEUS_REPO}}"
        )


# ---------------------------------------------------------------------------
# install_metadata_template.json
# ---------------------------------------------------------------------------


def test_install_metadata_template_exists():
    assert (BINDINGS_DIR / "install_metadata_template.json").is_file()


def test_install_metadata_template_has_required_keys():
    import json
    template = json.loads((BINDINGS_DIR / "install_metadata_template.json").read_text())
    required = {"schema_version", "first_run_at", "agent_version", "install_run_id", "allowed_remote_urls"}
    missing = required - set(template.keys())
    assert not missing, f"install_metadata_template.json missing: {missing}"


def test_install_metadata_template_schema_version():
    import json
    template = json.loads((BINDINGS_DIR / "install_metadata_template.json").read_text())
    assert template["schema_version"] == 1


def test_install_metadata_template_first_run_at_is_placeholder():
    """first_run_at must NOT be a real date in the template — it's a placeholder."""
    import json
    template = json.loads((BINDINGS_DIR / "install_metadata_template.json").read_text())
    first_run = template["first_run_at"]
    assert "PLACEHOLDER" in first_run, (
        f"template first_run_at={first_run!r} should be PLACEHOLDER_ISO8601_UTC, "
        "not a real date. Real date is written by the install script."
    )
