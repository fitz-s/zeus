"""P7 — Wallet as bankroll source of truth.

Tests that wallet_balance is the SOLE bankroll in live mode (no config cap).
2026-05-04 update (bankroll truth-chain cleanup): the prior `min(balance,
settings.capital_base_usd)` truncation has been removed; effective bankroll
now equals the on-chain wallet balance unconditionally. The daemon still
fails closed when the wallet query fails at startup.

Criteria covered:
  #1 — startup fails closed when wallet raises
  #2 — wallet balance flows through unchanged (no upper-bound clip)
  #3 — wallet error returns (None, entry_block_reason)
"""

import logging
import pytest
import src.engine.cycle_runtime as _runtime
from src.state.portfolio import PortfolioState


class _Settings:
    # capital_base_usd retained as a no-op test fixture attribute purely to
    # keep this stub class shape close to the historical Settings object.
    # cycle_runtime no longer reads this field (removed 2026-05-04).
    capital_base_usd = 200.0


class _FakeDeps:
    settings = _Settings()
    logger = logging.getLogger("test")

    @staticmethod
    def total_exposure_usd(_):
        return 0.0


class _LiveClob:
    paper_mode = False

    def __init__(self, balance=None, raises=False):
        self._balance = balance
        self._raises = raises

    def get_balance(self):
        if self._raises:
            raise RuntimeError("chain_unreachable")
        return self._balance


class _FailLiveClob:
    """Stub for startup fail-closed test."""
    paper_mode = False

    def get_balance(self):
        raise RuntimeError("chain_unreachable")


class TestWalletBankrollSource:
    def test_wallet_balance_is_primary_bankroll(self):
        """Criterion #2: wallet $50 → effective bankroll = $50 (unclipped)."""
        clob = _LiveClob(balance=50.0)
        portfolio = PortfolioState(bankroll=150.0)
        bankroll, cap = _runtime.entry_bankroll_for_cycle(portfolio, clob, deps=_FakeDeps)
        assert bankroll == 50.0
        assert cap["wallet_balance_usd"] == 50.0
        assert cap["bankroll_truth_source"] == "wallet_balance"
        assert cap["wallet_balance_used"] is True

    def test_wallet_balance_flows_through_unclipped(self):
        """2026-05-04: wallet $500 → effective bankroll = $500 (no config cap).

        Prior behaviour (deleted with the bankroll truth-chain cleanup):
        ``min(balance, settings.capital_base_usd)`` would have clipped this
        to $200. Live truth is now the wallet balance unconditionally.
        """
        clob = _LiveClob(balance=500.0)
        portfolio = PortfolioState(bankroll=150.0)
        bankroll, cap = _runtime.entry_bankroll_for_cycle(portfolio, clob, deps=_FakeDeps)
        assert bankroll == 500.0
        assert cap["wallet_balance_usd"] == 500.0
        assert cap["dynamic_cap_usd"] == 500.0
        assert cap["bankroll_truth_source"] == "wallet_balance"
        assert cap["entry_bankroll_contract"] == "live_wallet_only"

    def test_wallet_error_blocks_entries(self):
        """Wallet query exception u2192 returns (None, ...) with entry_block_reason=wallet_query_failed."""
        clob = _LiveClob(raises=True)
        portfolio = PortfolioState(bankroll=150.0)
        bankroll, cap = _runtime.entry_bankroll_for_cycle(portfolio, clob, deps=_FakeDeps)
        assert bankroll is None
        assert cap["entry_block_reason"] == "wallet_query_failed"

    @pytest.mark.parametrize("balance", [0.0, -1.0])
    def test_non_positive_wallet_balance_blocks_without_query_failure(self, balance):
        clob = _LiveClob(balance=balance)
        portfolio = PortfolioState(bankroll=150.0)
        bankroll, cap = _runtime.entry_bankroll_for_cycle(portfolio, clob, deps=_FakeDeps)
        assert bankroll is None
        assert cap["wallet_balance_usd"] == balance
        assert cap["entry_block_reason"] == "entry_bankroll_non_positive"

    def test_startup_fails_closed_on_wallet_error(self):
        """Criterion #1: wallet raises at startup u2192 daemon refuses to start via sys.exit."""
        import src.main as main_mod
        with pytest.raises(SystemExit):
            main_mod._startup_wallet_check(clob=_FailLiveClob())
