# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.3)
"""
Tests for maintenance_worker.rules.parser — load_task_catalog.

Coverage:
  - Schema version mismatch → CatalogSchemaError (FATAL)
  - Missing required fields (id, schedule) → CatalogSchemaError
  - Duplicate task_id within one catalog → DuplicateTaskIdError
  - Unknown/extra YAML keys tolerated (stored in raw)
  - Env-var expansion: ${REPO}, ${STATE_DIR}, ${EVIDENCE_DIR}, ${YEAR}, ${QUARTER}
  - Unknown vars (${ZEUS_REPO}) left as-is
  - TaskSpec round-trip: TaskCatalogEntry.spec is a valid TaskSpec
  - dry_run_floor_exempt=True propagated to TaskSpec
  - tags field propagated to TaskSpec
  - Missing file → FileNotFoundError
  - tasks must be a list → CatalogSchemaError
  - Real TASK_CATALOG.yaml loads 9 tasks without error
  - description fallback: rule_source → id when description absent
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from maintenance_worker.rules.parser import (
    CatalogSchemaError,
    DuplicateTaskIdError,
    TaskCatalogEntry,
    load_task_catalog,
)
from maintenance_worker.types.specs import TaskSpec


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

REAL_CATALOG_PATH = Path(
    "docs/operations/task_2026-05-15_runtime_improvement_engineering_package"
    "/02_daily_maintenance_agent/TASK_CATALOG.yaml"
)


def write_catalog(tmp_path: Path, content: str) -> Path:
    """Write a YAML catalog to a temp file and return its path."""
    p = tmp_path / "catalog.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def minimal_catalog(task_overrides: str = "") -> str:
    """Return a minimal valid catalog YAML string, with optional task overrides."""
    task_block = task_overrides or textwrap.dedent(
        """
        - id: task_alpha
          schedule: daily
          rule_source: some/rule
        """
    )
    return f"""\
schema_version: 1
tasks:
{textwrap.indent(task_block.strip(), '  ')}
"""


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_wrong_schema_version_raises(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 2
            tasks: []
            """,
        )
        with pytest.raises(CatalogSchemaError, match="schema_version"):
            load_task_catalog(p)

    def test_missing_schema_version_raises(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            tasks: []
            """,
        )
        with pytest.raises(CatalogSchemaError, match="schema_version"):
            load_task_catalog(p)

    def test_schema_version_1_accepted(self, tmp_path: Path) -> None:
        p = write_catalog(tmp_path, minimal_catalog())
        entries = load_task_catalog(p)
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


class TestFileIO:
    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_task_catalog(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_propagates(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("schema_version: 1\ntasks: [\n", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            load_task_catalog(p)

    def test_non_mapping_root_raises(self, tmp_path: Path) -> None:
        p = write_catalog(tmp_path, "- item1\n- item2\n")
        with pytest.raises(CatalogSchemaError, match="mapping"):
            load_task_catalog(p)


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


class TestRequiredFields:
    def test_missing_id_raises(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - schedule: daily
                rule_source: some/rule
            """,
        )
        with pytest.raises(CatalogSchemaError, match="id"):
            load_task_catalog(p)

    def test_missing_schedule_raises(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: my_task
                rule_source: some/rule
            """,
        )
        with pytest.raises(CatalogSchemaError, match="schedule"):
            load_task_catalog(p)

    def test_tasks_must_be_list(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              id: task_a
              schedule: daily
            """,
        )
        with pytest.raises(CatalogSchemaError, match="sequence"):
            load_task_catalog(p)

    def test_task_item_must_be_mapping(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - just_a_string
            """,
        )
        with pytest.raises(CatalogSchemaError, match="mapping"):
            load_task_catalog(p)


# ---------------------------------------------------------------------------
# Duplicate task_id
# ---------------------------------------------------------------------------


class TestDuplicateTaskId:
    def test_duplicate_id_raises(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: task_a
                schedule: daily
              - id: task_a
                schedule: weekly
            """,
        )
        with pytest.raises(DuplicateTaskIdError, match="task_a"):
            load_task_catalog(p)

    def test_unique_ids_accepted(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: task_a
                schedule: daily
              - id: task_b
                schedule: daily
            """,
        )
        entries = load_task_catalog(p)
        assert {e.spec.task_id for e in entries} == {"task_a", "task_b"}


# ---------------------------------------------------------------------------
# TaskSpec round-trip
# ---------------------------------------------------------------------------


class TestTaskSpecRoundTrip:
    def test_entry_contains_taskspec(self, tmp_path: Path) -> None:
        p = write_catalog(tmp_path, minimal_catalog())
        entries = load_task_catalog(p)
        assert len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, TaskCatalogEntry)
        assert isinstance(entry.spec, TaskSpec)

    def test_task_id_propagated(self, tmp_path: Path) -> None:
        p = write_catalog(tmp_path, minimal_catalog())
        entries = load_task_catalog(p)
        assert entries[0].spec.task_id == "task_alpha"

    def test_schedule_propagated(self, tmp_path: Path) -> None:
        p = write_catalog(tmp_path, minimal_catalog())
        entries = load_task_catalog(p)
        assert entries[0].spec.schedule == "daily"

    def test_description_from_rule_source(self, tmp_path: Path) -> None:
        p = write_catalog(tmp_path, minimal_catalog())
        entries = load_task_catalog(p)
        assert entries[0].spec.description == "some/rule"

    def test_description_fallback_to_id(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: fallback_task
                schedule: daily
            """,
        )
        entries = load_task_catalog(p)
        assert entries[0].spec.description == "fallback_task"

    def test_dry_run_floor_exempt_true(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: exempt_task
                schedule: daily
                dry_run_floor_exempt: true
            """,
        )
        entries = load_task_catalog(p)
        assert entries[0].spec.dry_run_floor_exempt is True

    def test_dry_run_floor_exempt_default_false(self, tmp_path: Path) -> None:
        p = write_catalog(tmp_path, minimal_catalog())
        entries = load_task_catalog(p)
        assert entries[0].spec.dry_run_floor_exempt is False

    def test_tags_propagated(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: tagged_task
                schedule: daily
                tags: [safety, audit]
            """,
        )
        entries = load_task_catalog(p)
        assert entries[0].spec.tags == ("safety", "audit")

    def test_tags_default_empty_tuple(self, tmp_path: Path) -> None:
        p = write_catalog(tmp_path, minimal_catalog())
        entries = load_task_catalog(p)
        assert entries[0].spec.tags == ()

    def test_spec_is_hashable(self, tmp_path: Path) -> None:
        """TaskSpec frozen=True means it must be usable in sets/dict keys."""
        p = write_catalog(tmp_path, minimal_catalog())
        entry = load_task_catalog(p)[0]
        s: set[TaskSpec] = {entry.spec}
        assert entry.spec in s


# ---------------------------------------------------------------------------
# Unknown / extra keys tolerated
# ---------------------------------------------------------------------------


class TestUnknownKeysTolerated:
    def test_extra_keys_in_raw(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: task_with_extras
                schedule: daily
                config:
                  ttl_days: 14
                safety:
                  forbidden_paths: ['src/**']
                evidence_emit: per_file_action
                unknown_future_key: some_value
            """,
        )
        entries = load_task_catalog(p)
        raw = entries[0].raw
        assert raw["config"]["ttl_days"] == 14
        assert raw["safety"]["forbidden_paths"] == ["src/**"]
        assert raw["evidence_emit"] == "per_file_action"
        assert raw["unknown_future_key"] == "some_value"

    def test_top_level_extra_keys_ignored(self, tmp_path: Path) -> None:
        """Top-level keys besides schema_version and tasks are silently ignored."""
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            project: my_project
            default_evidence_dir: /tmp/evidence
            guards:
              - name: guard_a
            tasks:
              - id: t1
                schedule: daily
            """,
        )
        entries = load_task_catalog(p)
        assert len(entries) == 1

    def test_empty_tasks_list(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks: []
            """,
        )
        entries = load_task_catalog(p)
        assert entries == []


# ---------------------------------------------------------------------------
# Env-var expansion
# ---------------------------------------------------------------------------


class TestEnvVarExpansion:
    def test_repo_expanded(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: t1
                schedule: daily
                config:
                  path: ${REPO}/state
            """,
        )
        entries = load_task_catalog(p, env={"REPO": "/tmp/myrepo"})
        assert entries[0].raw["config"]["path"] == "/tmp/myrepo/state"

    def test_state_dir_expanded(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: t1
                schedule: daily
                config:
                  state: ${STATE_DIR}/run.lock
            """,
        )
        entries = load_task_catalog(p, env={"STATE_DIR": "/var/state"})
        assert entries[0].raw["config"]["state"] == "/var/state/run.lock"

    def test_evidence_dir_expanded(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: t1
                schedule: daily
                config:
                  evidence: ${EVIDENCE_DIR}/audit
            """,
        )
        entries = load_task_catalog(p, env={"EVIDENCE_DIR": "/var/evidence"})
        assert entries[0].raw["config"]["evidence"] == "/var/evidence/audit"

    def test_year_and_quarter_expanded(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: t1
                schedule: daily
                config:
                  archive: docs/operations/archive/${YEAR}-Q${QUARTER}
            """,
        )
        entries = load_task_catalog(p, env={"YEAR": "2026", "QUARTER": "2"})
        assert entries[0].raw["config"]["archive"] == "docs/operations/archive/2026-Q2"

    def test_unknown_project_var_left_asis(self, tmp_path: Path) -> None:
        """${ZEUS_REPO} and other project vars are not resolved; left as-is."""
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: t1
                schedule: daily
                config:
                  path: ${ZEUS_REPO}/state
            """,
        )
        # Deliberately do NOT supply ZEUS_REPO in env
        entries = load_task_catalog(p, env={})
        assert entries[0].raw["config"]["path"] == "${ZEUS_REPO}/state"

    def test_multiple_vars_in_one_string(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: t1
                schedule: daily
                config:
                  path: ${REPO}/archive/${YEAR}/Q${QUARTER}
            """,
        )
        entries = load_task_catalog(
            p, env={"REPO": "/r", "YEAR": "2026", "QUARTER": "3"}
        )
        assert entries[0].raw["config"]["path"] == "/r/archive/2026/Q3"

    def test_expansion_in_list_values(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: t1
                schedule: daily
                config:
                  dirs: ['${STATE_DIR}/a', '${STATE_DIR}/b']
            """,
        )
        entries = load_task_catalog(p, env={"STATE_DIR": "/s"})
        assert entries[0].raw["config"]["dirs"] == ["/s/a", "/s/b"]

    def test_no_env_supplied_falls_back_to_os_environ(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REPO", "/env_repo")
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: t1
                schedule: daily
                config:
                  path: ${REPO}/data
            """,
        )
        entries = load_task_catalog(p)  # no env= supplied
        assert entries[0].raw["config"]["path"] == "/env_repo/data"


# ---------------------------------------------------------------------------
# Ordering preserved
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_catalog_order_preserved(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: task_one
                schedule: daily
              - id: task_two
                schedule: weekly
              - id: task_three
                schedule: daily
            """,
        )
        entries = load_task_catalog(p)
        assert [e.spec.task_id for e in entries] == [
            "task_one",
            "task_two",
            "task_three",
        ]


# ---------------------------------------------------------------------------
# Real TASK_CATALOG.yaml integration
# ---------------------------------------------------------------------------


class TestRealCatalog:
    def test_real_catalog_loads(self) -> None:
        """Real TASK_CATALOG.yaml must load without error and return 9 tasks."""
        if not REAL_CATALOG_PATH.exists():
            pytest.skip(f"Real catalog not found: {REAL_CATALOG_PATH}")
        entries = load_task_catalog(REAL_CATALOG_PATH, env={})
        assert len(entries) == 9

    def test_real_catalog_task_ids(self) -> None:
        """All expected task_ids present in real catalog."""
        if not REAL_CATALOG_PATH.exists():
            pytest.skip(f"Real catalog not found: {REAL_CATALOG_PATH}")
        entries = load_task_catalog(REAL_CATALOG_PATH, env={})
        task_ids = {e.spec.task_id for e in entries}
        expected = {
            "launchagent_backup_quarantine",
            "stale_worktree_quarantine",
            "in_repo_scratch_quarantine",
            "closed_packet_archive_proposal",
            "untracked_top_level_quarantine",
            "zero_byte_state_cleanup",
            "lore_proposal_emission",
            "authority_drift_surface",
            "agent_self_evidence_archival",
        }
        assert task_ids == expected

    def test_real_catalog_exempt_tasks_flagged(self) -> None:
        """zero_byte_state_cleanup and agent_self_evidence_archival are floor-exempt."""
        if not REAL_CATALOG_PATH.exists():
            pytest.skip(f"Real catalog not found: {REAL_CATALOG_PATH}")
        entries = load_task_catalog(REAL_CATALOG_PATH, env={})
        exempt = {e.spec.task_id for e in entries if e.spec.dry_run_floor_exempt}
        assert exempt == {"zero_byte_state_cleanup", "agent_self_evidence_archival"}

    def test_real_catalog_all_specs_are_valid_taskspec(self) -> None:
        """Every entry in the real catalog has a valid TaskSpec."""
        if not REAL_CATALOG_PATH.exists():
            pytest.skip(f"Real catalog not found: {REAL_CATALOG_PATH}")
        entries = load_task_catalog(REAL_CATALOG_PATH, env={})
        for entry in entries:
            assert isinstance(entry.spec, TaskSpec)
            assert entry.spec.task_id
            assert entry.spec.schedule
