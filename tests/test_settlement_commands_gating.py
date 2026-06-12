# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: phase5_h_decision.md §10 attack-pattern 2 (P5-M1);
#                  IMPLEMENTATION_PLAN Phase 5 pre-cutover patch M1

"""Regression tests for submit_redeem submission-boundary enforcement.

Updated 2026-06-12 (operator law 2026-06-10 ABSOLUTE — redeem submission FORBIDDEN):
  1. submit_redeem raises REDEEM_SUBMISSION_FORBIDDEN UNCONDITIONALLY, before any
     side effect — and before the kill-switch / on_chain_mutation gate is even
     consulted (the forbidden-raise is now the first statement). adapter.redeem is
     never reached.
  2. AST-walk: @capability("on_chain_mutation") is still present on submit_redeem
     (defense in depth — the decorator stays even though the body raises first).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.execution.settlement_commands import (
    assert_redeem_submission_allowed as _REAL_ASSERT_REDEEM,
)

REPO_ROOT = pathlib.Path(__file__).parent.parent


class TestSubmitRedeemGating:
    """Operator law: submit_redeem refuses BEFORE adapter.redeem, unconditionally."""

    def test_submit_redeem_forbidden_before_adapter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """submit_redeem raises REDEEM_SUBMISSION_FORBIDDEN before adapter.redeem,
        regardless of kill-switch state (the forbidden-raise precedes the gate)."""
        monkeypatch.setenv("ZEUS_KILL_SWITCH", "1")
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)

        # Restore the REAL guard (conftest installs a session-wide no-op patch so
        # accounting-setup suites can bootstrap via submit_redeem; _REAL_ASSERT_REDEEM
        # was captured at import time, before any patch, so it is the genuine guard).
        import src.execution.settlement_commands as sc

        monkeypatch.setattr(sc, "assert_redeem_submission_allowed", _REAL_ASSERT_REDEEM)

        # Sentinel: adapter.redeem must never be called.
        adapter_called = []

        class _SentinelAdapter:
            def redeem(self, condition_id: str, *, index_sets=None, **_ignored) -> None:
                adapter_called.append(condition_id)

        with pytest.raises(RuntimeError, match="REDEEM_SUBMISSION_FORBIDDEN"):
            sc.submit_redeem(
                command_id="test-cmd-id",
                adapter=_SentinelAdapter(),
                ledger=None,
            )

        assert adapter_called == [], (
            "adapter.redeem was called despite REDEEM_SUBMISSION_FORBIDDEN — "
            "the submission guard did not fire first"
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
