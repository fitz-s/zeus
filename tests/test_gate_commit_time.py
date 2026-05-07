# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §5 Gate 3; IMPLEMENTATION_PLAN §6 days 61-64;
#                  phase3_h_decision.md F-7 (non-py path-match-only)

"""Tests for Gate 3: commit-time diff verifier.

Three mandatory tests per deliverable spec:
  1. .py path rejection when @capability decorator is missing
  2. Non-.py path acceptance via path-match-only (F-7 mandatory condition)
  3. Commit message text matched against out_of_scope_keywords triggers reject
"""

from __future__ import annotations

import pathlib
import textwrap
import unittest.mock as mock

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent


class TestGateCommitTimePyDecoratorMissing:
    """Test 1: .py path in hard_kernel_paths without @capability decorator -> refuse."""

    def test_refuse_py_path_missing_decorator(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A .py kernel path without @capability decorator on any function -> blocked."""
        monkeypatch.delenv("ZEUS_ROUTE_GATE_COMMIT", raising=False)

        from src.architecture import gate_commit_time

        # Create the file at the path the gate will look for: REPO_ROOT / staged_path
        staged_rel = "src/state/ledger.py"
        fake_file = tmp_path / staged_rel
        fake_file.parent.mkdir(parents=True, exist_ok=True)
        fake_file.write_text(textwrap.dedent("""\
            def append_position_event(event):
                pass
        """))

        with mock.patch.object(gate_commit_time, "_has_capability_decorator_in_file", return_value=False):
            orig_root = gate_commit_time.REPO_ROOT
            gate_commit_time.REPO_ROOT = tmp_path
            try:
                allowed, messages = gate_commit_time.evaluate(
                    staged_paths=[staged_rel],
                    commit_msg="fix ledger append",
                )
            finally:
                gate_commit_time.REPO_ROOT = orig_root

        assert not allowed, f"Expected refuse for .py missing decorator, got allow. Messages: {messages}"
        blocked = [m for m in messages if "BLOCKED" in m]
        assert blocked, f"No BLOCKED message found: {messages}"
        assert any("decorator" in m for m in blocked), f"Expected decorator mention in block: {blocked}"

    def test_allow_py_path_with_decorator(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A .py kernel path with @capability decorator -> allowed."""
        monkeypatch.delenv("ZEUS_ROUTE_GATE_COMMIT", raising=False)

        from src.architecture import gate_commit_time

        with mock.patch.object(gate_commit_time, "_has_capability_decorator_in_file", return_value=True):
            orig_root = gate_commit_time.REPO_ROOT
            gate_commit_time.REPO_ROOT = tmp_path
            try:
                allowed, messages = gate_commit_time.evaluate(
                    staged_paths=["src/state/ledger.py"],
                    commit_msg="fix ledger append",
                )
            finally:
                gate_commit_time.REPO_ROOT = orig_root

        assert allowed, f"Expected allow for .py with decorator, got refuse. Messages: {messages}"


class TestGateCommitTimeNonPyPathMatch:
    """Test 2: non-.py capability paths use path-match-only, no AST walk (F-7)."""

    def test_non_py_path_accepted_without_ast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-.py hard_kernel_path is accepted via path-match-only (F-7 mandatory condition).

        authority_doc_rewrite capability owns AGENTS.md (non-.py) -- path match should
        emit allow with check_type=path_match and NOT call AST walk.
        """
        monkeypatch.delenv("ZEUS_ROUTE_GATE_COMMIT", raising=False)

        from src.architecture import gate_commit_time

        ast_called = []
        original_ast_fn = gate_commit_time._has_capability_decorator_in_file

        def spy_ast(*args, **kwargs):
            ast_called.append(args)
            return original_ast_fn(*args, **kwargs)

        with mock.patch.object(gate_commit_time, "_has_capability_decorator_in_file", side_effect=spy_ast):
            allowed, messages = gate_commit_time.evaluate(
                staged_paths=["AGENTS.md"],
                commit_msg="update agents doc",
            )

        assert allowed, f"Expected allow for non-.py path, got refuse. Messages: {messages}"
        assert not ast_called, (
            f"AST walk was called for non-.py path (F-7 violation). "
            f"Called with: {ast_called}"
        )
        path_match_msgs = [m for m in messages if "path-match-only" in m or "non-py" in m.lower()]
        assert path_match_msgs, f"Expected path-match-only message: {messages}"

    def test_db_file_accepted_path_match_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A .db kernel path is path-match-only (non-.py, F-7)."""
        monkeypatch.delenv("ZEUS_ROUTE_GATE_COMMIT", raising=False)

        from src.architecture import gate_commit_time

        ast_called = []
        with mock.patch.object(
            gate_commit_time,
            "_has_capability_decorator_in_file",
            side_effect=lambda *a, **k: ast_called.append(a) or False,
        ):
            allowed, messages = gate_commit_time.evaluate(
                staged_paths=["state/zeus_trades.db"],
                commit_msg="migration checkpoint",
            )

        assert allowed, f"Expected allow for .db path, got refuse. Messages: {messages}"
        assert not ast_called, f"AST walk must not fire on non-.py (F-7): {ast_called}"


class TestGateCommitTimeIntentKeyword:
    """Test 3: commit message containing out_of_scope_keywords -> reject."""

    def test_refuse_commit_with_out_of_scope_keyword(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Commit message containing 'paper' for canonical_position_write -> blocked."""
        monkeypatch.delenv("ZEUS_ROUTE_GATE_COMMIT", raising=False)

        from src.architecture import gate_commit_time

        with mock.patch.object(gate_commit_time, "_has_capability_decorator_in_file", return_value=True):
            orig_root = gate_commit_time.REPO_ROOT
            gate_commit_time.REPO_ROOT = REPO_ROOT
            try:
                allowed, messages = gate_commit_time.evaluate(
                    staged_paths=["src/state/ledger.py"],
                    commit_msg="paper mode: skip ledger append for backtest",
                )
            finally:
                gate_commit_time.REPO_ROOT = orig_root

        assert not allowed, (
            f"Expected refuse for out_of_scope keyword 'paper' in commit msg, "
            f"got allow. Messages: {messages}"
        )
        blocked = [m for m in messages if "BLOCKED" in m]
        assert blocked, f"No BLOCKED message for intent keyword match: {messages}"

    def test_allow_commit_with_scope_keywords(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Commit message with only in-scope keywords -> allowed."""
        monkeypatch.delenv("ZEUS_ROUTE_GATE_COMMIT", raising=False)

        from src.architecture import gate_commit_time

        with mock.patch.object(gate_commit_time, "_has_capability_decorator_in_file", return_value=True):
            orig_root = gate_commit_time.REPO_ROOT
            gate_commit_time.REPO_ROOT = REPO_ROOT
            try:
                allowed, messages = gate_commit_time.evaluate(
                    staged_paths=["src/state/ledger.py"],
                    commit_msg="fix: ledger position append idempotency",
                )
            finally:
                gate_commit_time.REPO_ROOT = orig_root

        assert allowed, f"Expected allow for in-scope commit message, got refuse. Messages: {messages}"


class TestGateCommitTimeFeatureFlag:
    """ZEUS_ROUTE_GATE_COMMIT=off skips all checks."""

    def test_flag_off_skips_all_checks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ZEUS_ROUTE_GATE_COMMIT", "off")

        from src.architecture.gate_commit_time import evaluate

        allowed, messages = evaluate(
            staged_paths=["src/state/ledger.py"],
            commit_msg="anything",
        )
        assert allowed
        assert any("SKIPPED" in m for m in messages)


class TestGateCommitTimeRitualSignal:
    """ritual_signal emitted per evaluation with correct schema."""

    def test_signal_has_required_fields(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json

        monkeypatch.delenv("ZEUS_ROUTE_GATE_COMMIT", raising=False)

        import src.architecture.gate_commit_time as g
        orig_dir = g._RITUAL_SIGNAL_DIR
        g._RITUAL_SIGNAL_DIR = tmp_path / "ritual_signal"
        try:
            with mock.patch.object(g, "_has_capability_decorator_in_file", return_value=True):
                g.evaluate(
                    staged_paths=["src/state/ledger.py"],
                    commit_msg="fix ledger",
                )
        finally:
            g._RITUAL_SIGNAL_DIR = orig_dir

        signal_files = list((tmp_path / "ritual_signal").glob("*.jsonl"))
        assert signal_files, "No ritual_signal .jsonl emitted"
        lines = signal_files[0].read_text().strip().splitlines()
        assert lines

        record = json.loads(lines[0])
        for field in ("helper", "cap_id", "path", "check_type", "decision", "charter_version"):
            assert field in record, f"ritual_signal missing field: {field!r}"
        assert record["helper"] == "gate_commit_time"
        assert record["charter_version"] == "1.0.0"
