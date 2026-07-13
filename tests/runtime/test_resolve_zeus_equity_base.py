# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md
#   "Bankroll/Kelly 三量分立" (2026-07-13 operator fork resolution);
#   src/runtime/bankroll_provider.py::resolve_zeus_equity_base

"""Antibody tests for the explicit Zeus capital-allocation mechanism (LX-T2-a).

Covers:
- resolve_zeus_equity_base pure math for all three modes + validation errors.
- default-settings passthrough (no allocation kwarg -> reads config/settings.json).
- wiring proof: _fetch_balance's NEW-ENTRY sizing equity under the default
  mode="wallet_total" is BYTE-EQUAL to the pre-LX-T2-a formula
  (free_pusd + sizing_position_value) — the "do not touch kelly math itself"
  boundary is proven here, not just asserted.
"""

from __future__ import annotations

import importlib

import pytest

from src.runtime import bankroll_provider
from src.runtime.bankroll_provider import resolve_zeus_equity_base


def test_wallet_total_mode_is_byte_equal_passthrough(caplog):
    wallet_equity = 1234.5678
    with caplog.at_level("WARNING"):
        result = resolve_zeus_equity_base(wallet_equity, allocation={"mode": "wallet_total"})
    assert result == wallet_equity
    assert "ZEUS_EQUITY_DEGRADED_ATTRIBUTION" in caplog.text


def test_wallet_total_is_the_default_when_allocation_omitted(monkeypatch):
    # No allocation kwarg -> reads config/settings.json. Force the settings
    # source to something WITHOUT the new key to prove the omission defaults
    # to wallet_total (additive-key backward compatibility).
    monkeypatch.setattr(bankroll_provider, "_load_zeus_capital_allocation_setting", lambda: {"mode": "wallet_total"})
    assert resolve_zeus_equity_base(500.0) == 500.0


def test_fraction_mode_scales_wallet_equity():
    assert resolve_zeus_equity_base(1000.0, allocation={"mode": "fraction", "value": 0.25}) == 250.0
    assert resolve_zeus_equity_base(1000.0, allocation={"mode": "fraction", "value": 0.0}) == 0.0
    assert resolve_zeus_equity_base(1000.0, allocation={"mode": "fraction", "value": 1.0}) == 1000.0


def test_fraction_mode_rejects_out_of_range_value():
    with pytest.raises(ValueError, match="fraction value must be in"):
        resolve_zeus_equity_base(1000.0, allocation={"mode": "fraction", "value": 1.5})
    with pytest.raises(ValueError, match="fraction value must be in"):
        resolve_zeus_equity_base(1000.0, allocation={"mode": "fraction", "value": -0.1})


def test_absolute_mode_returns_explicit_value_under_wallet_equity():
    assert resolve_zeus_equity_base(1000.0, allocation={"mode": "absolute", "value": 300.0}) == 300.0


def test_absolute_mode_never_invents_equity_beyond_wallet_total():
    # An over-committed absolute allocation is capped to observed wallet equity —
    # this function must never fabricate equity the wallet does not hold.
    assert resolve_zeus_equity_base(1000.0, allocation={"mode": "absolute", "value": 5000.0}) == 1000.0


def test_absolute_mode_rejects_negative_value():
    with pytest.raises(ValueError, match="absolute value must be >= 0"):
        resolve_zeus_equity_base(1000.0, allocation={"mode": "absolute", "value": -1.0})


def test_missing_value_raises_for_fraction_and_absolute():
    with pytest.raises(ValueError, match="requires a numeric 'value'"):
        resolve_zeus_equity_base(1000.0, allocation={"mode": "fraction"})
    with pytest.raises(ValueError, match="requires a numeric 'value'"):
        resolve_zeus_equity_base(1000.0, allocation={"mode": "absolute"})


def test_invalid_mode_raises():
    with pytest.raises(ValueError, match="mode must be one of"):
        resolve_zeus_equity_base(1000.0, allocation={"mode": "bogus"})


class _FakeClient:
    """Fake PolymarketClient returning fixed wallet balance / positions."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_wallet_balance(self) -> float:
        return 456.78

    def get_positions_from_api(self):
        return []


def test_fetch_balance_wallet_total_mode_matches_pre_lx_t2a_formula(monkeypatch):
    """Wiring proof: _fetch_balance's sizing-equity leg is unchanged under wallet_total.

    Restores the REAL _fetch_balance (the autouse test-isolation fixture stubs
    it to raise) and patches the PolymarketClient it constructs internally, so
    this exercises the genuine function body — not a mock of resolve_zeus_equity_base.
    """
    importlib.reload(bankroll_provider)
    monkeypatch.setattr(bankroll_provider, "_load_zeus_capital_allocation_setting", lambda: {"mode": "wallet_total"})
    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient", _FakeClient
    )

    equity_for_loss_threshold, free_pusd, equity_for_new_entry_sizing = bankroll_provider._fetch_balance()

    # Pre-LX-T2-a formula (empty positions -> sizing_position_value == 0.0):
    # equity_for_new_entry_sizing == free_pusd + 0.0 == free_pusd.
    assert free_pusd == 456.78
    assert equity_for_new_entry_sizing == free_pusd
    assert equity_for_loss_threshold == free_pusd

    importlib.reload(bankroll_provider)


def test_fetch_balance_fraction_mode_shrinks_only_sizing_equity_not_loss_threshold(monkeypatch):
    """Loss-threshold equity (global safety telemetry) must NOT move with the
    capital-allocation mode -- only the Kelly sizing leg is explicit-allocation
    aware (docs/rebuild/local_ledger_excision_2026-07-12.md 三量分立)."""
    importlib.reload(bankroll_provider)
    monkeypatch.setattr(
        bankroll_provider,
        "_load_zeus_capital_allocation_setting",
        lambda: {"mode": "fraction", "value": 0.5},
    )
    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient", _FakeClient
    )

    equity_for_loss_threshold, free_pusd, equity_for_new_entry_sizing = bankroll_provider._fetch_balance()

    assert equity_for_loss_threshold == free_pusd  # unchanged, wallet-aggregate
    assert equity_for_new_entry_sizing == pytest.approx(free_pusd * 0.5)

    importlib.reload(bankroll_provider)
