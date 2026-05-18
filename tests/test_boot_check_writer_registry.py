# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: /Users/leofitz/.claude/jobs/9ea6f95c/briefs/f44_recurrence_prevention.md §Slice 3
"""A5 boot-check antibody tests — assert_writer_jobs_registered.

Three test cases per the F44 recurrence-prevention brief:
  1. Happy path:  every writable table declared in a mini-YAML has a
     matching @_scheduler_job("...") in the injected ingest_main source.
  2. Red path:    registry declares daemon_writer but no matching decorator
                  exists → RegistryAssertionError raised with the table name.
  3. Sed-break:   the *actual* ingest_main.py has the 'ingest_k2_obs_v2_tick'
                  decorator present; removing it would cause the red path.
                  This test asserts the live source has the decorator, and that
                  its absence (simulated) causes the check to fail.

Antibody proof (sed-break / restore):
  Comment out the @_scheduler_job("ingest_k2_obs_v2_tick") decorator on the
  real _ingest_k2_obs_v2_tick function in src/ingest_main.py.  Test 3 fails
  immediately.  Restore → test 3 passes.
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
    """Return minimal db_table_ownership.yaml text for injection."""
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
    raw = {"tables": {"world": [entry]}}
    return yaml.dump(raw)


def _make_ingest_source(has_decorator: bool) -> str:
    """Return minimal ingest_main.py source text with/without the decorator."""
    decorator_line = '@_scheduler_job("ingest_k2_obs_v2_tick")' if has_decorator else "# decorator removed"
    return textwrap.dedent(f"""\
        def _scheduler_job(name):
            def decorator(fn):
                return fn
            return decorator

        {decorator_line}
        def _ingest_k2_obs_v2_tick(context):
            pass
    """)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestA5BootCheckWriterRegistry:
    """A5 (v1.F44): assert_writer_jobs_registered boot-check antibody."""

    def test_happy_path(self, tmp_path, monkeypatch):
        """Happy path: every writable table has a matching scheduler job → passes."""
        yaml_path = tmp_path / "db_table_ownership.yaml"
        yaml_path.write_text(_make_yaml("ingest_k2_obs_v2_tick"))

        import src.state.table_registry as reg_mod
        monkeypatch.setattr(reg_mod, "_REGISTRY_PATH", yaml_path)

        source = _make_ingest_source(has_decorator=True)
        # Should not raise
        assert_writer_jobs_registered(ingest_main_source=source)

    def test_red_path_missing_job(self, tmp_path, monkeypatch):
        """Red path: registry declares daemon_writer but no matching @_scheduler_job → raises."""
        yaml_path = tmp_path / "db_table_ownership.yaml"
        yaml_path.write_text(_make_yaml("ingest_k2_obs_v2_tick"))

        import src.state.table_registry as reg_mod
        monkeypatch.setattr(reg_mod, "_REGISTRY_PATH", yaml_path)

        source = _make_ingest_source(has_decorator=False)
        with pytest.raises(RegistryAssertionError) as exc_info:
            assert_writer_jobs_registered(ingest_main_source=source)

        msg = str(exc_info.value)
        assert "observation_instants_v2" in msg
        assert "ingest_k2_obs_v2_tick" in msg
        assert "not wired" in msg or "daemon_writer" in msg

    def test_no_daemon_writer_entries_passes(self, tmp_path, monkeypatch):
        """Tables without daemon_writer field are silently skipped → passes."""
        yaml_path = tmp_path / "db_table_ownership.yaml"
        # No daemon_writer field at all
        yaml_path.write_text(_make_yaml(daemon_writer=None))

        import src.state.table_registry as reg_mod
        monkeypatch.setattr(reg_mod, "_REGISTRY_PATH", yaml_path)

        # Any source — should not be reached
        source = _make_ingest_source(has_decorator=False)
        assert_writer_jobs_registered(ingest_main_source=source)

    def test_daemon_writer_none_string_skipped(self, tmp_path, monkeypatch):
        """daemon_writer: 'none' (string) is treated as no-daemon-writer → passes."""
        yaml_path = tmp_path / "db_table_ownership.yaml"
        yaml_path.write_text(_make_yaml("none"))

        import src.state.table_registry as reg_mod
        monkeypatch.setattr(reg_mod, "_REGISTRY_PATH", yaml_path)

        source = _make_ingest_source(has_decorator=False)
        assert_writer_jobs_registered(ingest_main_source=source)

    def test_sed_break_live_source(self, tmp_path, monkeypatch):
        """Sed-break: confirm the LIVE ingest_main.py has 'ingest_k2_obs_v2_tick' wired.

        This test also simulates its absence to prove the check fires.
        Antibody proof: comment out @_scheduler_job("ingest_k2_obs_v2_tick")
        in src/ingest_main.py → first assertion fails immediately.
        """
        ingest_main_path = _REPO_ROOT / "src" / "ingest_main.py"
        live_source = ingest_main_path.read_text(encoding="utf-8")

        # Verify the decorator is present in the live source (sed-break guard)
        tree = ast.parse(live_source)
        registered: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Name)
                    and dec.func.id == "_scheduler_job"
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                ):
                    registered.add(str(dec.args[0].value))

        assert "ingest_k2_obs_v2_tick" in registered, (
            "ANTIBODY FAILURE: @_scheduler_job('ingest_k2_obs_v2_tick') not found in "
            "src/ingest_main.py — the v2 obs writer is not wired as a scheduler job!"
        )

        # Now confirm removal causes the check to fire
        yaml_path = tmp_path / "db_table_ownership.yaml"
        yaml_path.write_text(_make_yaml("ingest_k2_obs_v2_tick"))

        import src.state.table_registry as reg_mod
        monkeypatch.setattr(reg_mod, "_REGISTRY_PATH", yaml_path)

        broken_source = _make_ingest_source(has_decorator=False)
        with pytest.raises(RegistryAssertionError):
            assert_writer_jobs_registered(ingest_main_source=broken_source)
