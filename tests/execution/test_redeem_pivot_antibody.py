# Created: 2026-06-10
# Last reused or audited: 2026-06-12
# Authority basis: operator law 2026-06-10 (ABSOLUTE): "Zeus NEVER submits redeem
#   transactions again" — redemption is EXTERNAL; Zeus only does EXTERNAL_REDEMPTION
#   accounting. External deep-review finding (2026-06-12): the residual override
#   escape hatch (ZEUS_OPERATOR_REDEEM_OVERRIDE token) + autonomous-broadcast body
#   must be made UNCONSTRUCTABLE, not merely double-gated.
"""Redeem-submission-FORBIDDEN antibody: no codepath can broadcast a redeem tx.

The guard is now UNCONDITIONAL — there is no env, flag, or override token that
re-arms redemption. Three layers, each pinned:
1. submit_redeem raises REDEEM_SUBMISSION_FORBIDDEN BEFORE any side effect
   (no adapter contact, no DB write), regardless of any env/override.
2. The venue adapter's redeem() raises at the (now-deleted) tx-broadcast boundary.
3. The scheduler job calm-skips (redeem_submission_allowed() is always False).

NOTE: tests/conftest.py installs an autouse fixture that monkeypatches
``assert_redeem_submission_allowed`` to a no-op so the receipt-classification
ACCOUNTING suites can bootstrap REDEEM_TX_HASHED fixture state. These antibody
tests RESTORE the real guard first (``_restore_real_guard``) so they observe the
genuine unconditional raise — the production teeth live here, not in that fixture.
"""

import importlib

import pytest

import src.execution.settlement_commands as sc
from src.execution.settlement_commands import (
    RedeemSubmissionAbandonedError,
    assert_redeem_submission_allowed as _real_assert,
    redeem_submission_allowed,
    submit_redeem,
)

_AUTONOMOUS_FLAG = "ZEUS_AUTONOMOUS_REDEEM_ENABLED"
_STRAY_OVERRIDE_ENV = "ZEUS_OPERATOR_REDEEM_OVERRIDE"


@pytest.fixture(autouse=True)
def _restore_real_guard(monkeypatch):
    """Undo conftest's session-wide no-op patch so these tests see the real,
    unconditional guard. Without this, submit_redeem would proceed past the
    guard (the conftest patch exists only for accounting-setup suites)."""
    monkeypatch.setattr(sc, "assert_redeem_submission_allowed", _real_assert)


class _ExplodingAdapter:
    """Any contact proves the antibody failed — no venue/RPC method may be invoked."""

    def __getattr__(self, name):
        raise AssertionError(f"adapter must never be touched (attr {name!r} accessed)")


class TestLayer1SubmitRedeem:
    def test_refuses_unconditionally(self, monkeypatch):
        monkeypatch.delenv(_STRAY_OVERRIDE_ENV, raising=False)
        with pytest.raises(
            RedeemSubmissionAbandonedError, match="REDEEM_SUBMISSION_FORBIDDEN"
        ):
            submit_redeem("cmd-x", _ExplodingAdapter(), None)

    def test_refuses_even_with_autonomous_flag_on(self, monkeypatch):
        """The old kill-switch flag must never re-arm redemption."""
        monkeypatch.setenv(_AUTONOMOUS_FLAG, "1")
        with pytest.raises(RedeemSubmissionAbandonedError):
            submit_redeem("cmd-x", _ExplodingAdapter(), None)

    def test_refuses_even_with_any_override_token(self, monkeypatch):
        """The override escape hatch is DELETED — no token value re-arms it."""
        monkeypatch.setenv(_STRAY_OVERRIDE_ENV, "operator-confirmed-manual-redeem")
        monkeypatch.setenv(_AUTONOMOUS_FLAG, "1")
        with pytest.raises(RedeemSubmissionAbandonedError):
            submit_redeem("cmd-x", _ExplodingAdapter(), None)

    def test_raise_is_a_runtime_error(self, monkeypatch):
        """Contract type: the raise is a RuntimeError subclass."""
        monkeypatch.delenv(_STRAY_OVERRIDE_ENV, raising=False)
        with pytest.raises(RuntimeError, match="REDEEM_SUBMISSION_FORBIDDEN"):
            submit_redeem("cmd-x", _ExplodingAdapter(), None)


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
    def test_scheduler_job_calm_skips(self, monkeypatch):
        """_redeem_submitter_cycle returns immediately without touching locks,
        credentials, or the DB — redeem_submission_allowed() is always False."""
        monkeypatch.delenv(_STRAY_OVERRIDE_ENV, raising=False)
        import src.main as main_module

        assert main_module._redeem_submitter_cycle() is None

    def test_helper_is_unconditionally_false(self, monkeypatch):
        """No env value flips redeem_submission_allowed() True anymore."""
        monkeypatch.setenv(_STRAY_OVERRIDE_ENV, "operator-confirmed-manual-redeem")
        monkeypatch.setenv(_AUTONOMOUS_FLAG, "1")
        assert redeem_submission_allowed() is False
        with pytest.raises(RedeemSubmissionAbandonedError, match="REDEEM_SUBMISSION_FORBIDDEN"):
            _real_assert("test")
