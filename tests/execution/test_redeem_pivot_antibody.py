# Created: 2026-06-10
# Last reused or audited: 2026-07-08
# Authority basis: operator law 2026-06-10 (ABSOLUTE): "Zeus NEVER submits redeem
#   transactions again" — redemption is EXTERNAL; Zeus only does EXTERNAL_REDEMPTION
#   accounting. External deep-review finding (2026-06-12): the residual override
#   escape hatch (ZEUS_OPERATOR_REDEEM_OVERRIDE token) + autonomous-broadcast body
#   must be made UNCONSTRUCTABLE, not merely double-gated.
#   R6-a (2026-07-08): the dead redeem-submission machinery this file's Layer 1/3
#   tests used to exercise (submit_redeem, _redeem_submitter_cycle,
#   reseat_stub_deferred_rows_for_autonomous_retry, ~650 lines of
#   construct/sign/broadcast in polymarket_v2_adapter.py's _redeem_via_safe /
#   _redeem_via_negrisk_safe) was DELETED, not merely re-gated. Per the R0-d
#   antibody precedent, deletion IS the strongest antibody — the behavioral
#   "raises before any side effect" tests for that machinery are replaced below
#   with structural absence assertions: the submission surface no longer exists
#   to raise from. Layer 2 (the venue adapter's redeem() entry point) still
#   exists as the permanent typed guard for any legacy/compat caller, so its
#   behavioral test is UNCHANGED.
"""Redeem-submission-FORBIDDEN antibody: no codepath can broadcast a redeem tx.

Two enforcement shapes now, each pinned:
1. STRUCTURAL ABSENCE — the submission surfaces do not exist:
   submit_redeem, _redeem_submitter_cycle, reseat_stub_deferred_rows_for_
   autonomous_retry, and polymarket_v2_adapter's _redeem_via_safe /
   _redeem_via_negrisk_safe broadcast bodies are all deleted symbols. No
   "redeem_submitter" scheduler job is registered in the P4 daemon.
2. BEHAVIORAL RAISE — the venue adapter's redeem() entry point (kept as a
   permanent typed guard for any caller still reaching it) raises
   REDEEM_SUBMISSION_FORBIDDEN unconditionally, and never contains a quoted
   eth_sendRawTransaction broadcast call in its own body.
3. The accounting guard (assert_redeem_submission_allowed /
   redeem_submission_allowed) is KEPT and still unconditional — it is what
   adapter.redeem() routes through, and remains the citable enforcement
   point for any future caller.

NOTE: tests/conftest.py installs an autouse fixture that monkeypatches
``assert_redeem_submission_allowed`` to a no-op so the receipt-classification
ACCOUNTING suites can bootstrap REDEEM_TX_HASHED fixture state. The Layer 2/3
tests below RESTORE the real guard first (``_restore_real_guard``) so they
observe the genuine unconditional raise — the production teeth live here, not
in that fixture.
"""

import ast
import pathlib

import pytest

import src.execution.settlement_commands as sc
from src.execution.settlement_commands import (
    RedeemSubmissionAbandonedError,
    assert_redeem_submission_allowed as _real_assert,
    redeem_submission_allowed,
)

_AUTONOMOUS_FLAG = "ZEUS_AUTONOMOUS_REDEEM_ENABLED"
_STRAY_OVERRIDE_ENV = "ZEUS_OPERATOR_REDEEM_OVERRIDE"

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
def _restore_real_guard(monkeypatch):
    """Undo conftest's session-wide no-op patch so these tests see the real,
    unconditional guard. Without this, a legacy caller would proceed past
    the guard (the conftest patch exists only for accounting-setup suites)."""
    monkeypatch.setattr(sc, "assert_redeem_submission_allowed", _real_assert)


class TestLayer1SubmitRedeemStructuralAbsence:
    """R6-a: submit_redeem and its private helpers no longer exist. Deletion
    is the antibody -- there is no "raises before side effect" behavior to
    test because there is no submission function to call at all."""

    def test_submit_redeem_symbol_does_not_exist(self):
        assert not hasattr(sc, "submit_redeem"), (
            "submit_redeem must stay deleted (operator law 2026-06-10, R6-a); "
            "if this fails, redeem-submission machinery was re-introduced"
        )

    def test_reseat_autonomous_retry_symbol_does_not_exist(self):
        assert not hasattr(sc, "reseat_stub_deferred_rows_for_autonomous_retry"), (
            "reseat_stub_deferred_rows_for_autonomous_retry must stay deleted "
            "(R6-a) -- it existed only to autonomously re-arm submit_redeem"
        )

    def test_venue_adapter_broadcast_bodies_do_not_exist(self):
        from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

        for name in ("_redeem_via_safe", "_redeem_via_negrisk_safe"):
            assert not hasattr(PolymarketV2Adapter, name), (
                f"{name} must stay deleted (R6-a) -- it built real "
                "eth_sendRawTransaction redeem calldata with zero production "
                "callers (redeem() below always raised before reaching it)"
            )

    def test_venue_adapter_module_has_no_broadcast_call_anywhere(self):
        """Source-text sweep of the whole adapter module (not just redeem()):
        no eth_sendRawTransaction literal may survive outside the wrap/CTF
        split/merge/convert paths (which are KEEP, W2 design, unrelated to
        redeem). This guards against the broadcast body being re-added under
        a new method name."""
        src_path = REPO_ROOT / "src" / "venue" / "polymarket_v2_adapter.py"
        tree = ast.parse(src_path.read_text())
        redeem_adjacent_methods = {"redeem", "_redeem_via_safe", "_redeem_via_negrisk_safe"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in redeem_adjacent_methods:
                # Only "redeem" can exist post-R6-a; the other two must be absent
                # (covered by test_venue_adapter_broadcast_bodies_do_not_exist).
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Constant) and sub.value == "eth_sendRawTransaction":
                        pytest.fail(
                            f"{node.name}() contains a quoted eth_sendRawTransaction "
                            "literal -- redeem broadcast is FORBIDDEN (operator law "
                            "2026-06-10)"
                        )


class TestLayer2VenueAdapter:
    def test_adapter_redeem_refuses_unconditionally(self, monkeypatch):
        monkeypatch.setenv(_AUTONOMOUS_FLAG, "1")
        monkeypatch.setenv(_STRAY_OVERRIDE_ENV, "operator-confirmed-manual-redeem")
        from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

        adapter = PolymarketV2Adapter.__new__(PolymarketV2Adapter)  # no creds needed
        with pytest.raises(
            RedeemSubmissionAbandonedError, match="REDEEM_SUBMISSION_FORBIDDEN"
        ):
            adapter.redeem("0xdeadbeef", index_sets=[1], neg_risk=False)

    def test_adapter_redeem_has_no_broadcast_call_in_body(self):
        """Source-text antibody: the redeem() method body no longer contains an
        eth_sendRawTransaction broadcast (it was deleted; the unconditional raise
        is the only behavior). Guards against a future re-introduction of the
        autonomous-broadcast body into the redeem() entry point itself."""
        import inspect

        from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

        src = inspect.getsource(PolymarketV2Adapter.redeem)
        # Strip comments + docstrings so a comment that merely NAMES the deleted
        # call doesn't trip the antibody. We assert no QUOTED "eth_sendRawTransaction"
        # JSON-RPC method literal (the actual broadcast call form) survives.
        code_lines = []
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            code_lines.append(line)
        code_only = "\n".join(code_lines)
        assert '"eth_sendRawTransaction"' not in code_only, (
            "redeem() body must not contain a quoted eth_sendRawTransaction "
            "broadcast call — the broadcast path is deleted (operator law 2026-06-10)"
        )
        assert "assert_redeem_submission_allowed" in code_only, (
            "redeem() must route through the unconditional submission-forbidden guard"
        )


class TestLayer3Scheduler:
    def test_redeem_submitter_cycle_does_not_exist(self):
        """R6-a structural absence: _redeem_submitter_cycle (post_trade_capital.py)
        and its scheduler registration are deleted, not merely calm-skipping --
        it already unconditionally calm-skipped every cycle
        (redeem_submission_allowed() is always False), so deletion changes
        nothing about production behavior."""
        import src.execution.post_trade_capital as post_trade_capital

        assert not hasattr(post_trade_capital, "_redeem_submitter_cycle")

    def test_no_redeem_submitter_scheduler_registration(self):
        """No job id "redeem_submitter" is registered anywhere in the P4
        post-trade-capital daemon's boot sequence, and _redeem_submitter_cycle
        is not imported (a stray mention in an explanatory comment is fine --
        this checks the AST for an actual import, not any text occurrence)."""
        src_path = REPO_ROOT / "src" / "ingest" / "post_trade_capital_daemon.py"
        text = src_path.read_text()
        assert 'id="redeem_submitter"' not in text
        tree = ast.parse(text)
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "_redeem_submitter_cycle" not in imported_names

    def test_helper_is_unconditionally_false(self, monkeypatch):
        """No env value flips redeem_submission_allowed() True anymore."""
        monkeypatch.setenv(_STRAY_OVERRIDE_ENV, "operator-confirmed-manual-redeem")
        monkeypatch.setenv(_AUTONOMOUS_FLAG, "1")
        assert redeem_submission_allowed() is False
        with pytest.raises(RedeemSubmissionAbandonedError, match="REDEEM_SUBMISSION_FORBIDDEN"):
            _real_assert("test")
