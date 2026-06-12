# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: docs/operations/consolidated_systemic_overhaul_2026-06-11.md K3.6(a)
# (operator directive 2026-06-10 ~22:55Z "完全抛弃redeem": Zeus must NEVER submit
# redeem transactions; third-party auto-redeem owns the shared wallet).
"""K3.6 redeem-pivot antibody: no daemon codepath can broadcast a redeem tx.

Three layers, each pinned:
1. submit_redeem raises BEFORE any side effect (no adapter contact, no DB write).
2. The venue adapter's redeem() raises at the tx-broadcast boundary itself.
3. The scheduler job calm-skips (and the legacy ZEUS_AUTONOMOUS_REDEEM_ENABLED
   flag alone is NOT sufficient — a flag flip must never re-arm redemption).
The override is operator-domain: the exact token value, not truthiness.
"""

import pytest

from src.execution.settlement_commands import (
    REDEEM_PIVOT_OPERATOR_OVERRIDE_ENV,
    REDEEM_PIVOT_OPERATOR_OVERRIDE_TOKEN,
    RedeemSubmissionAbandonedError,
    assert_redeem_submission_allowed,
    redeem_submission_allowed,
    submit_redeem,
)


class _ExplodingAdapter:
    """Any contact proves the antibody failed."""

    def __getattr__(self, name):
        raise AssertionError(f"adapter must never be touched (attr {name!r} accessed)")


class TestLayer1SubmitRedeem:
    def test_refuses_without_override(self, monkeypatch):
        monkeypatch.delenv(REDEEM_PIVOT_OPERATOR_OVERRIDE_ENV, raising=False)
        with pytest.raises(RedeemSubmissionAbandonedError, match="REDEEM_SUBMISSION_ABANDONED"):
            submit_redeem("cmd-x", _ExplodingAdapter(), None)

    def test_refuses_even_with_autonomous_flag_on(self, monkeypatch):
        """The old kill-switch flag alone must never be the only barrier."""
        monkeypatch.delenv(REDEEM_PIVOT_OPERATOR_OVERRIDE_ENV, raising=False)
        monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")
        with pytest.raises(RedeemSubmissionAbandonedError):
            submit_redeem("cmd-x", _ExplodingAdapter(), None)

    def test_truthy_override_value_is_not_enough(self, monkeypatch):
        """Exact-token requirement: a stray '1' export cannot re-arm redemption."""
        monkeypatch.setenv(REDEEM_PIVOT_OPERATOR_OVERRIDE_ENV, "1")
        with pytest.raises(RedeemSubmissionAbandonedError):
            submit_redeem("cmd-x", _ExplodingAdapter(), None)

    def test_operator_token_passes_the_gate(self, monkeypatch):
        """With the exact operator token, the guard opens (the call then proceeds
        to the normal machinery, which fails on the nonexistent command — proving
        we got PAST the antibody, not that redemption works headlessly)."""
        monkeypatch.setenv(
            REDEEM_PIVOT_OPERATOR_OVERRIDE_ENV, REDEEM_PIVOT_OPERATOR_OVERRIDE_TOKEN
        )
        with pytest.raises(Exception) as excinfo:
            submit_redeem("cmd-that-does-not-exist", _ExplodingAdapter(), None)
        assert not isinstance(excinfo.value, RedeemSubmissionAbandonedError)


class TestLayer2VenueAdapter:
    def test_adapter_redeem_refuses_without_override(self, monkeypatch):
        monkeypatch.delenv(REDEEM_PIVOT_OPERATOR_OVERRIDE_ENV, raising=False)
        monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")
        from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

        adapter = PolymarketV2Adapter.__new__(PolymarketV2Adapter)  # no creds needed
        with pytest.raises(RedeemSubmissionAbandonedError):
            adapter.redeem("0xdeadbeef", index_sets=[1], neg_risk=False)


class TestLayer3Scheduler:
    def test_scheduler_job_calm_skips(self, monkeypatch):
        """_redeem_submitter_cycle returns immediately without touching locks,
        credentials, or the DB when the override is unset."""
        monkeypatch.delenv(REDEEM_PIVOT_OPERATOR_OVERRIDE_ENV, raising=False)
        import src.main as main_module

        # The wrapped function is the @_scheduler_job target; calling it must
        # neither raise nor attempt credential resolution (which would explode
        # in a test environment without keychain access).
        assert main_module._redeem_submitter_cycle() is None

    def test_helper_semantics(self, monkeypatch):
        monkeypatch.delenv(REDEEM_PIVOT_OPERATOR_OVERRIDE_ENV, raising=False)
        assert redeem_submission_allowed() is False
        with pytest.raises(RedeemSubmissionAbandonedError):
            assert_redeem_submission_allowed("test")
        monkeypatch.setenv(
            REDEEM_PIVOT_OPERATOR_OVERRIDE_ENV, REDEEM_PIVOT_OPERATOR_OVERRIDE_TOKEN
        )
        assert redeem_submission_allowed() is True
        assert_redeem_submission_allowed("test")  # no raise
