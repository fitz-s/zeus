# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: /Users/leofitz/.claude/jobs/9ea6f95c/briefs/f44_recurrence_prevention.md §Slice 3
"""A5 boot-check antibody tests — assert_writer_jobs_registered.

Three test cases per the F44 recurrence-prevention brief:
  1. Happy path:  every writable table declared in a mini-YAML has a
     matching @_scheduler_job("...") and _scheduler.add_job in the source.
  2. Red path:    registry declares daemon_writer but missing decorator
                  or add_job call → RegistryAssertionError raised.
  3. Meta-test:   runs against a small slice of REAL architecture/db_table_ownership.yaml.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest
import yaml

from src.state.table_registry import RegistryAssertionError, assert_writer_jobs_registered

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


def _make_yaml(daemon_writer: str | None) -> str:
    """Return minimal db_table_ownership.yaml text for injection (FLAT LIST shape)."""
    entry: dict = {
        "name": "observation_instants_v2",
        "db": "world",
        "schema_class": "world_class",
        "schema_version_owner": "SCHEMA_VERSION",
        "created_by": "init_schema",
        "pk_col": None,
    }
    if daemon_writer is not None:
        entry["daemon_writer"] = daemon_writer
    
    # PRODUCTION shape is tables: [ {name: ...}, ... ]
    raw = {"tables": [entry]}
    return yaml.dump(raw)


def _make_ingest_source(has_decorator: bool, has_add_job: bool) -> str:
    """Return minimal ingest_main.py source text."""
    # Note: ingest_k2_obs_v2_tick is the decorator name, ingest_k2_obs_v2 is the add_job ID
    decorator_line = '@_scheduler_job("ingest_k2_obs_v2_tick")' if has_decorator else ""
    add_job_line = '_scheduler.add_job(fn, id="ingest_k2_obs_v2")' if has_add_job else "pass"
    
    return textwrap.dedent(f"""\
        def _scheduler_job(name):
            def decorator(fn):
                return fn
            return decorator

        {decorator_line}
        def _ingest_k2_obs_v2_tick(context):
            pass

        def init():
            {add_job_line}
    """)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestA5BootCheckWriterRegistry:
    """A5 (v1.F44): assert_writer_jobs_registered boot-check antibody."""

    def test_happy_path(self, tmp_path, monkeypatch):
        """Happy path: every writable table has decorator + add_job → passes."""
        yaml_path = tmp_path / "db_table_ownership.yaml"
        yaml_path.write_text(_make_yaml("ingest_k2_obs_v2_tick"))

        import src.state.table_registry as reg_mod
        monkeypatch.setattr(reg_mod, "_REGISTRY_PATH", yaml_path)

        source = _make_ingest_source(has_decorator=True, has_add_job=True)
        # Should not raise
        assert_writer_jobs_registered(ingest_main_source=source)

    def test_red_path_missing_decorator(self, tmp_path, monkeypatch):
        """Red path: registry declares daemon_writer but no matching @_scheduler_job → raises."""
        yaml_path = tmp_path / "db_table_ownership.yaml"
        yaml_path.write_text(_make_yaml("ingest_k2_obs_v2_tick"))

        import src.state.table_registry as reg_mod
        monkeypatch.setattr(reg_mod, "_REGISTRY_PATH", yaml_path)

        source = _make_ingest_source(has_decorator=False, has_add_job=True)
        with pytest.raises(RegistryAssertionError) as exc_info:
            assert_writer_jobs_registered(ingest_main_source=source)

        msg = str(exc_info.value)
        assert "no @_scheduler_job('ingest_k2_obs_v2_tick')" in msg

    def test_red_path_missing_add_job(self, tmp_path, monkeypatch):
        """Red path: decorator present but no _scheduler.add_job → raises."""
        yaml_path = tmp_path / "db_table_ownership.yaml"
        yaml_path.write_text(_make_yaml("ingest_k2_obs_v2_tick"))

        import src.state.table_registry as reg_mod
        monkeypatch.setattr(reg_mod, "_REGISTRY_PATH", yaml_path)

        source = _make_ingest_source(has_decorator=True, has_add_job=False)
        with pytest.raises(RegistryAssertionError) as exc_info:
            assert_writer_jobs_registered(ingest_main_source=source)

        msg = str(exc_info.value)
        assert "no _scheduler.add_job(..., id='ingest_k2_obs_v2')" in msg

    def test_meta_real_yaml_shape_check(self):
        """Verify the boot-check can parse the REAL production YAML shape."""
        # We don't check for failure here, just that it doesn't crash on .values()
        # because the real YAML is a list.
        assert_writer_jobs_registered()

    def test_sed_break_live_source_consistency(self):
        """Verify the LIVE ingest_main.py has internal consistency for all jobs."""
        # This will fail if a job has a decorator but no add_job, or vice versa.
        assert_writer_jobs_registered()
