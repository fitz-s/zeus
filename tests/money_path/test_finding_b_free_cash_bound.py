# Created: 2026-06-12
# Last reused/audited: 2026-06-12
# Authority basis: external deep code review 2026-06-12 FINDING-B (operator direct-fix
#   order). The free-cash one-time bound silently VANISHED (free_cash_usd=None, no
#   clamp) whenever a bankroll_usd_provider was injected. The fix threads a companion
#   free_cash_usd_provider; a live free-cash authority that returns None is a typed
#   transient fault (BANKROLL_FREE_CASH_MISSING), never a silent unclamped submit.
"""FINDING-B relationship invariant: when the bankroll basis comes from an injected
provider AND a companion free-cash authority is wired, the chosen stake is bounded by
free cash (min, applied once); a free-cash authority that cannot resolve fails CLOSED
with a typed TRANSIENT reason rather than sizing unclamped.

These reuse the full receipt-sizing harness from test_event_reactor_no_bypass (the same
fixtures that exercise the live Kelly path), so the assertions pin the END-TO-END
relationship (provider in -> bounded stake / typed fault out), not a unit shim.
"""

from __future__ import annotations

import pytest

from src.events.reactor import _is_transient_money_path_reason

# Reuse the proven receipt-sizing harness + fixtures.
from tests.engine.test_event_reactor_no_bypass import (  # noqa: E402
    _bound_forecast_event,
    _receipt,
    _trade_conn_with_snapshot,
)


@pytest.fixture(autouse=True)
def _isolate_edli_settings(monkeypatch):
    """Mirror the no_bypass module's isolation so the fixture reaches the Kelly path
    (EMOS sole-calibrator / bias-correction / soft-anchor trade authority forced OFF —
    the fixture has no calibration/bias rows and must not be overridden by live flags)."""
    from src.config import settings

    edli = dict(settings._data["edli"])
    edli["edli_emos_sole_calibrator_enabled"] = False
    edli["edli_bias_correction_enabled"] = False
    monkeypatch.setitem(settings._data, "edli", edli)
    feature_flags = dict(settings._data["feature_flags"])
    feature_flags["openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled"] = False
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)


def test_free_cash_provider_binds_stake_to_free_cash():
    """Provider total equity = 1000, free cash = 5, strong edge whose unclamped stake is
    ~14 USD: the chosen stake MUST be clamped to <= free cash (the one-time cash bound)."""
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: 1000.0,
        free_cash_usd_provider=lambda: 5.0,
    )
    assert receipt.kelly_pass is True
    assert receipt.kelly_size_usd is not None
    assert receipt.kelly_size_usd <= 5.0 + 1e-9


def test_free_cash_above_stake_does_not_inflate():
    """When free cash exceeds the fractional-Kelly stake, the bound is a no-op (min):
    the stake stays at its equity-scaled value, never raised to free cash."""
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: 1000.0,
        free_cash_usd_provider=lambda: 1000.0,
    )
    assert receipt.kelly_pass is True
    # The unclamped equity-scaled stake for this fixture is ~14 USD (well below 1000).
    assert receipt.kelly_size_usd is not None
    assert 0.0 < receipt.kelly_size_usd < 1000.0


def test_free_cash_unresolvable_under_live_provider_fails_closed_transient():
    """A wired free-cash authority that returns None is a TYPED FAULT, never a silent
    unclamped submit. The receipt does not pass, and the reason is classified TRANSIENT
    (requeue) so the next warm cycle re-resolves the wallet rather than terminal-burning."""
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: 1000.0,
        free_cash_usd_provider=lambda: None,
    )
    assert receipt.kelly_pass is False
    assert "BANKROLL_FREE_CASH_MISSING" in (receipt.reason or "")
    assert _is_transient_money_path_reason(receipt.reason) is True


def test_no_free_cash_provider_is_legacy_no_clamp():
    """Back-compat: a bankroll provider WITHOUT a free-cash provider (proof-only / tool
    injection that wired no cash authority) keeps the legacy no-clamp behavior — it does
    NOT fail closed, so existing proof-only callers are unaffected."""
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: 1000.0,
    )
    assert receipt.kelly_pass is True
    assert receipt.kelly_size_usd is not None
    assert receipt.kelly_size_usd > 0.0
