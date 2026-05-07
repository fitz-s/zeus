# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: phase5_h_decision.md §10 attack-pattern 2 (P5-M1);
#                  IMPLEMENTATION_PLAN Phase 5 pre-cutover patch M1

"""Regression tests for P5-M1: submit_redeem on_chain_mutation gate enforcement.

Two mandatory tests per P5-M1 deliverable spec:
  1. ZEUS_KILL_SWITCH=1 → gate_runtime.check raises BEFORE adapter.redeem is called.
  2. AST-walk: @capability("on_chain_mutation") is present on submit_redeem.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent


class TestSubmitRedeemGating:
    """P5-M1: gate_runtime.check("on_chain_mutation") fires before adapter.redeem."""

    def test_submit_redeem_gated_when_kill_switch_on(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """With ZEUS_KILL_SWITCH=1, submit_redeem raises before adapter.redeem is reached."""
        monkeypatch.setenv("ZEUS_KILL_SWITCH", "1")
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)

        # Redirect ritual_signal writes to tmp dir.
        monkeypatch.setattr(
            "src.architecture.gate_runtime._RITUAL_SIGNAL_DIR",
            tmp_path / "ritual_signal",
        )
        from src.architecture import gate_runtime
        importlib.reload(gate_runtime)
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        # Sentinel: adapter.redeem must never be called if gate fires first.
        adapter_called = []

        class _SentinelAdapter:
            def redeem(self, condition_id: str) -> None:
                adapter_called.append(condition_id)

        import src.execution.settlement_commands as sc
        importlib.reload(sc)

        with pytest.raises(RuntimeError, match="kill_switch_active"):
            sc.submit_redeem(
                command_id="test-cmd-id",
                adapter=_SentinelAdapter(),
                ledger=None,
            )

        assert adapter_called == [], (
            "adapter.redeem was called despite kill_switch_active — gate did not fire first"
        )

    def test_submit_redeem_decorator_present(self) -> None:
        """AST-walk: submit_redeem carries @capability("on_chain_mutation", ...)."""
        source_path = REPO_ROOT / "src" / "execution" / "settlement_commands.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))

        found = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != "submit_redeem":
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if isinstance(func, ast.Name) and func.id == "capability":
                    args = dec.args
                    if args and isinstance(args[0], ast.Constant) and args[0].value == "on_chain_mutation":
                        found = True
                        break

        assert found, (
            "@capability('on_chain_mutation', ...) decorator not found on submit_redeem "
            f"in {source_path}"
        )
