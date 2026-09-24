# Created: 2026-05-06
# Last reused or audited: 2026-08-10
# Authority basis: live side effects are blocked by kill/freeze/risk authority;
#                  new exposure requires current executable-code authority.

"""Tests for Gate 5: runtime kill-switch and settlement-window-freeze enforcement.

Three mandatory tests per deliverable spec (D-2):
  1. Refuse live_venue_submit when kill_switch_active.
  2. Refuse settlement_write when settlement_window_freeze_active.
  3. Allow both when all-clear.
"""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest


REPO_ROOT = pathlib.Path(__file__).parent.parent


class TestGateRuntimeKillSwitch:
    """Test 1: gate_runtime.check("live_venue_submit") raises when kill switch armed."""

    def test_refuse_live_venue_submit_on_kill_switch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """kill_switch_active blocks live_venue_submit with RuntimeError."""
        monkeypatch.setenv("ZEUS_KILL_SWITCH", "1")
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        # Redirect ritual_signal writes to tmp dir so tests don't pollute real logs.
        monkeypatch.setattr(
            "src.architecture.gate_runtime._RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal"
        )

        from src.architecture import gate_runtime
        import importlib
        importlib.reload(gate_runtime)
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="kill_switch_active"):
            gate_runtime.check("live_venue_submit")

    def test_refuse_reduce_only_exit_submit_on_kill_switch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """kill_switch_active blocks reduce-only exit submit too."""
        monkeypatch.setenv("ZEUS_KILL_SWITCH", "1")
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="kill_switch_active"):
            gate_runtime.check("reduce_only_exit_submit")

    def test_refuse_live_venue_submit_on_risk_halt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """risk_level_halt blocks live_venue_submit with RuntimeError."""
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.setenv("ZEUS_RISK_HALT", "true")
        monkeypatch.setattr(
            "src.architecture.gate_runtime._RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal"
        )

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="risk_level_halt"):
            gate_runtime.check("live_venue_submit")


class TestGateRuntimeSettlementFreeze:
    """Test 2: gate_runtime.check("settlement_write") raises when freeze active."""

    def test_refuse_settlement_write_on_freeze(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """settlement_window_freeze_active blocks settlement_write with RuntimeError."""
        monkeypatch.setenv("ZEUS_SETTLEMENT_FREEZE", "on")
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="settlement_window_freeze_active"):
            gate_runtime.check("settlement_write")

    def test_refuse_reduce_only_exit_submit_on_freeze(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        monkeypatch.setenv("ZEUS_SETTLEMENT_FREEZE", "on")
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="settlement_window_freeze_active"):
            gate_runtime.check("reduce_only_exit_submit")


class TestGateRuntimeAllClear:
    """Test 3: allow when all conditions are clear."""

    def test_allow_live_venue_submit_all_clear(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """All conditions clear => no exception raised; ritual_signal emitted with allow."""
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)

        from src.architecture import gate_runtime
        sig_dir = tmp_path / "ritual_signal"
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", sig_dir)

        # Should not raise
        gate_runtime.check("live_venue_submit")
        gate_runtime.check("settlement_write")

        # Verify ritual_signal written
        jsonl_files = list(sig_dir.glob("*.jsonl"))
        assert jsonl_files, "Expected ritual_signal log to be written"
        lines = jsonl_files[0].read_text().strip().splitlines()
        assert len(lines) >= 2
        records = [json.loads(l) for l in lines]
        decisions = {r["decision"] for r in records}
        assert "allow" in decisions, f"Expected at least one 'allow' decision; got {decisions}"

    def test_deployment_freshness_mismatch_blocks_new_exposure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", "a" * 40)

        from src.architecture import gate_runtime
        from src.control import runtime_code_plane

        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")
        monkeypatch.setattr(gate_runtime, "REPO_ROOT", tmp_path)
        def _fake_git(cmd, **_kwargs):
            if list(cmd[:3]) == ["git", "diff", "--name-only"]:
                return b"src/main.py\n"
            return ("b" * 40).encode()

        monkeypatch.setattr(
            runtime_code_plane.subprocess,
            "check_output",
            _fake_git,
        )

        with pytest.raises(RuntimeError, match="deployment_freshness_mismatch"):
            gate_runtime.check("live_venue_submit")

    def test_runtime_diff_blocks_entry_but_preserves_reduce_only_exit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", "a" * 40)

        from src.architecture import gate_runtime
        from src.control import runtime_code_plane

        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")
        monkeypatch.setattr(gate_runtime, "REPO_ROOT", tmp_path)

        def _fake_git(cmd, **_kwargs):
            if list(cmd[:3]) == ["git", "diff", "--name-only"]:
                return b"src/data/mainstream_forecast_source.py\n"
            return ("b" * 40).encode()

        monkeypatch.setattr(runtime_code_plane.subprocess, "check_output", _fake_git)
        monkeypatch.setattr(
            runtime_code_plane,
            "dirty_runtime_worktree_paths",
            lambda *_args, **_kwargs: (),
        )

        with pytest.raises(RuntimeError, match="deployment_freshness_mismatch"):
            gate_runtime.check("live_venue_submit")
        gate_runtime.check("reduce_only_exit_submit")

    def test_reduce_only_exit_allows_exit_runtime_diff(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", "a" * 40)

        from src.architecture import gate_runtime
        from src.control import runtime_code_plane

        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")
        monkeypatch.setattr(gate_runtime, "REPO_ROOT", tmp_path)

        def _fake_git(cmd, **_kwargs):
            if list(cmd[:3]) == ["git", "diff", "--name-only"]:
                return b"src/execution/executor.py\n"
            return ("b" * 40).encode()

        monkeypatch.setattr(runtime_code_plane.subprocess, "check_output", _fake_git)
        monkeypatch.setattr(
            runtime_code_plane,
            "dirty_runtime_worktree_paths",
            lambda *_args, **_kwargs: (),
        )

        gate_runtime.check("reduce_only_exit_submit")

    def test_reduce_only_exit_allows_dirty_exit_runtime_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        sha = "c" * 40
        monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", sha)

        from src.architecture import gate_runtime
        from src.control import runtime_code_plane

        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")
        monkeypatch.setattr(gate_runtime, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(
            runtime_code_plane.subprocess,
            "check_output",
            lambda *_, **__: sha.encode(),
        )
        monkeypatch.setattr(
            runtime_code_plane,
            "dirty_runtime_worktree_paths",
            lambda *_args, **_kwargs: ("src/execution/exit_lifecycle.py",),
        )

        gate_runtime.check("reduce_only_exit_submit")

    def test_deployment_freshness_match_allows_live_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        sha = "c" * 40
        monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", sha)

        from src.architecture import gate_runtime
        from src.control import runtime_code_plane

        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")
        monkeypatch.setattr(gate_runtime, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(
            runtime_code_plane.subprocess,
            "check_output",
            lambda *_, **__: sha.encode(),
        )

        gate_runtime.check("live_venue_submit")

    def test_dirty_runtime_worktree_blocks_new_exposure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        sha = "c" * 40
        monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", sha)

        from src.architecture import gate_runtime
        from src.control import runtime_code_plane

        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")
        monkeypatch.setattr(gate_runtime, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(
            runtime_code_plane.subprocess,
            "check_output",
            lambda *_, **__: sha.encode(),
        )
        monkeypatch.setattr(
            runtime_code_plane,
            "dirty_runtime_worktree_paths",
            lambda *_args, **_kwargs: ("src/control/live_health.py",),
        )

        with pytest.raises(RuntimeError, match="deployment_freshness_mismatch"):
            gate_runtime.check("live_venue_submit")

    def test_deployment_freshness_dirty_readonly_audit_scripts_allow_live_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        sha = "c" * 40
        monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", sha)

        from src.architecture import gate_runtime
        from src.control import runtime_code_plane

        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")
        monkeypatch.setattr(gate_runtime, "REPO_ROOT", tmp_path)

        def _fake_git_status(cmd, **_kwargs):
            if list(cmd[:2]) == ["git", "status"]:
                return type(
                    "Proc",
                    (),
                    {
                        "returncode": 0,
                        "stdout": "?? scripts/audit_live_probability_reality.py\n"
                        "?? scripts/audit_yes_no_selection_skew.py\n",
                    },
                )()
            raise AssertionError(f"unexpected command: {cmd!r}")

        monkeypatch.setattr(
            runtime_code_plane.subprocess,
            "check_output",
            lambda *_, **__: sha.encode(),
        )
        monkeypatch.setattr(runtime_code_plane.subprocess, "run", _fake_git_status)

        gate_runtime.check("live_venue_submit")

    def test_deployment_freshness_offline_generator_diff_allows_live_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", "a" * 40)

        from src.architecture import gate_runtime
        from src.control import runtime_code_plane

        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")
        monkeypatch.setattr(gate_runtime, "REPO_ROOT", tmp_path)

        def _fake_git(cmd, **_kwargs):
            if list(cmd[:3]) == ["git", "diff", "--name-only"]:
                return b"scripts/gen_economics_writer_manifest.py\n"
            return ("b" * 40).encode()

        monkeypatch.setattr(runtime_code_plane.subprocess, "check_output", _fake_git)

        gate_runtime.check("live_venue_submit")

    @pytest.mark.parametrize(
        "path",
        sorted(
            {
                "scripts/download_replacement_forecast_current_targets.py",
                "scripts/drain_settlement_disputes.py",
                "scripts/hko_ingest_tick.py",
                "scripts/migrations/__init__.py",
                "scripts/obs_live_tick.py",
                "scripts/validate_assumptions.py",
            }
        ),
    )
    def test_deployment_freshness_keeps_daemon_imported_scripts_in_runtime_plane(
        self, path: str
    ) -> None:
        from src.control.runtime_code_plane import is_runtime_code_path

        assert is_runtime_code_path(path)

    @pytest.mark.parametrize(
        "registry_path",
        ["architecture/test_topology.yaml", "architecture/script_manifest.yaml"],
    )
    def test_deployment_freshness_dirty_operator_registry_allows_live_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, registry_path: str
    ) -> None:
        """Operator inventories do not alter the daemon's executable code plane."""

        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        sha = "c" * 40
        monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", sha)

        from src.architecture import gate_runtime
        from src.control import runtime_code_plane

        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")
        monkeypatch.setattr(gate_runtime, "REPO_ROOT", tmp_path)

        def _fake_git_status(cmd, **_kwargs):
            if list(cmd[:2]) == ["git", "status"]:
                return type(
                    "Proc",
                    (),
                    {
                        "returncode": 0,
                        "stdout": f" M {registry_path}\n",
                    },
                )()
            raise AssertionError(f"unexpected command: {cmd!r}")

        monkeypatch.setattr(
            runtime_code_plane.subprocess,
            "check_output",
            lambda *_, **__: sha.encode(),
        )
        monkeypatch.setattr(runtime_code_plane.subprocess, "run", _fake_git_status)

        assert runtime_code_plane.dirty_runtime_worktree_paths(tmp_path) == ()
        assert not runtime_code_plane.is_runtime_code_path(registry_path)
        assert not runtime_code_plane.is_reduce_only_exit_runtime_path(registry_path)
        gate_runtime.check("live_venue_submit")

    @pytest.mark.parametrize(
        "path",
        [
            "src/execution/executor.py",
            "config/settings.json",
            "architecture/db_table_ownership.yaml",
            "architecture/runtime_posture.yaml",
            "architecture/strategy_profile_registry.yaml",
            "architecture/cascade_liveness_contract.yaml",
            "architecture/2026_04_02_architecture_kernel.sql",
        ],
    )
    def test_dirty_runtime_authority_remains_in_deployment_plane(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, path: str
    ) -> None:
        from src.control import runtime_code_plane

        monkeypatch.setattr(
            runtime_code_plane.subprocess,
            "run",
            lambda *args, **kwargs: type(
                "Proc", (), {"returncode": 0, "stdout": f" M {path}\n"}
            )(),
        )
        assert runtime_code_plane.is_runtime_code_path(path)
        assert runtime_code_plane.dirty_runtime_worktree_paths(tmp_path) == (path,)

    @pytest.mark.parametrize(
        "changed_path",
        [
            "tests/test_only.py",
            "architecture/script_manifest.yaml",
            # 2026-09-24: a prose-only edit to this governance file blocked every
            # live BUY for hours (boot 4d5de532, HEAD a2d7314f, src/ unchanged).
            "architecture/capabilities.yaml",
            "architecture/source_rationale.yaml",
            "architecture/invariants.yaml",
            "architecture/some_future_governance_note.yaml",
        ],
    )
    def test_deployment_freshness_non_runtime_diff_allows_live_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, changed_path: str
    ) -> None:
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", "a" * 40)

        from src.architecture import gate_runtime
        from src.control import runtime_code_plane

        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")
        monkeypatch.setattr(gate_runtime, "REPO_ROOT", tmp_path)

        def _fake_git(cmd, **_kwargs):
            if list(cmd[:3]) == ["git", "diff", "--name-only"]:
                return f"{changed_path}\n".encode()
            return ("b" * 40).encode()

        monkeypatch.setattr(runtime_code_plane.subprocess, "check_output", _fake_git)

        gate_runtime.check("live_venue_submit")

    def test_deployment_freshness_agent_instruction_diff_allows_live_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", "a" * 40)

        from src.architecture import gate_runtime
        from src.control import runtime_code_plane

        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")
        monkeypatch.setattr(gate_runtime, "REPO_ROOT", tmp_path)

        def _fake_git(cmd, **_kwargs):
            if list(cmd[:3]) == ["git", "diff", "--name-only"]:
                return (
                    b".agents/skills/zeus-methodology-bootstrap/SKILL.md\n"
                    b".claude/agents/verifier.md\n"
                )
            return ("b" * 40).encode()

        monkeypatch.setattr(runtime_code_plane.subprocess, "check_output", _fake_git)

        gate_runtime.check("live_venue_submit")

    def test_allow_emits_ritual_signal_with_required_schema(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Emitted ritual_signal must have cap_id, gate_id, decision, ts fields."""
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)

        from src.architecture import gate_runtime
        sig_dir = tmp_path / "ritual_signal"
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", sig_dir)

        gate_runtime.check("live_venue_submit")

        jsonl_files = list(sig_dir.glob("*.jsonl"))
        assert jsonl_files
        record = json.loads(jsonl_files[0].read_text().strip().splitlines()[-1])
        for field in ("cap_id", "gate_id", "decision", "ts", "invocation_ts", "charter_version"):
            assert field in record, f"Missing required ritual_signal field: {field!r}"
        assert record["gate_id"] == "gate5_runtime"
        assert record["cap_id"] == "live_venue_submit"
        assert record["decision"] == "allow"


class TestGateRuntimeSettlementFreezeBlocksLiveEntry:
    """Test: ZEUS_SETTLEMENT_FREEZE=1 blocks live_venue_submit (execute_intent / execute_final_intent paths).

    Per capabilities.yaml live_venue_submit.blocked_when: [settlement_window_freeze_active]
    and ULTIMATE_DESIGN §5 Gate 5 (line 181).  PR #71 review P1 fix.
    """

    def test_settlement_freeze_blocks_live_venue_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """ZEUS_SETTLEMENT_FREEZE=1 must raise RuntimeError on live_venue_submit."""
        monkeypatch.setenv("ZEUS_SETTLEMENT_FREEZE", "1")
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="settlement_window_freeze_active"):
            gate_runtime.check("live_venue_submit")

    def test_settlement_freeze_true_blocks_live_venue_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """ZEUS_SETTLEMENT_FREEZE=true also blocks (all truthy variants)."""
        monkeypatch.setenv("ZEUS_SETTLEMENT_FREEZE", "true")
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="settlement_window_freeze_active"):
            gate_runtime.check("live_venue_submit")

    def test_settlement_freeze_off_allows_live_venue_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """ZEUS_SETTLEMENT_FREEZE unset does not block live_venue_submit."""
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        # Should not raise
        gate_runtime.check("live_venue_submit")


def test_every_architecture_file_loaded_by_live_code_is_in_the_runtime_plane() -> None:
    """The allow-list must cover every architecture/ file that src/ opens.

    A daemon that loads a file the classifier calls non-runtime would keep
    trading on stale law after that file changes. Scan the live code for
    architecture/ path literals and require each to be classified runtime.
    """
    import re

    from src.control.runtime_code_plane import (
        RUNTIME_ARCHITECTURE_FILES,
        RUNTIME_SCRIPT_FILES,
        is_runtime_code_path,
    )

    repo = pathlib.Path(__file__).resolve().parents[1]
    pattern = re.compile(
        r"""["']architecture["']\s*\)?\s*/\s*["']([\w.\-]+)["']"""
        r"""|/\s*["']architecture/([\w.\-]+)["']"""
    )
    offline_only = {
        # Edit/commit-time governance gates, never imported by a daemon.
        "src/architecture/route_function.py",
        "src/architecture/gate_commit_time.py",
        "src/architecture/gate_edit_time.py",
    }
    loaded: set[str] = set()
    sources = [*repo.glob("src/**/*.py"), *(repo / p for p in RUNTIME_SCRIPT_FILES)]
    for source in sources:
        rel = source.relative_to(repo).as_posix()
        if rel in offline_only or not source.is_file():
            continue
        for match in pattern.finditer(source.read_text(encoding="utf-8")):
            loaded.add("architecture/" + (match.group(1) or match.group(2)))
    assert loaded, "scan found no architecture/ loads; the pattern is stale"
    missing = sorted(path for path in loaded if not is_runtime_code_path(path))
    assert missing == [], f"live code loads non-runtime architecture files: {missing}"
    stale = sorted(RUNTIME_ARCHITECTURE_FILES - loaded)
    assert stale == [], f"allow-list names files no live code loads: {stale}"
