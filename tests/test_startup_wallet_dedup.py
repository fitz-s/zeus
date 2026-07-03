# Created: 2026-06-05
# Last reused/audited: 2026-06-19
# Authority basis: efficiency #1 boot wallet-fetch dedupe + live monitor continuity on wallet RPC faults
# Lifecycle: created=2026-06-05; last_reviewed=2026-06-19; last_reused=2026-06-19
# Purpose: Relationship antibody — the production (clob=None) wallet gate routes through bankroll_provider.current() and reuses Site A's warm 30s cache instead of issuing a SECOND on-chain RPC.
# Reuse: Re-run when _startup_wallet_check's production path, the Site A "Capital (on-chain)" warm call, or bankroll_provider's cache TTL changes.
"""Relationship test — boot wallet-fetch dedupe (Site A warm → Site B free).

Cross-module invariant under test:
  Site A (main.run, ~line 6122) calls bankroll_provider.current() purely for the
  "Capital (on-chain)" log line. That call WARMS the bankroll_provider module
  global (30s TTL). Site B (_startup_wallet_check, production clob=None path) is
  the fail-closed wallet-reachability gate; it must reuse that warm cache rather
  than issuing a SECOND on-chain RPC.

The boundary property asserted here:  the production (clob=None) path of
_startup_wallet_check ROUTES THROUGH bankroll_provider.current() — it does NOT
construct its own PolymarketClient + get_balance(). Because Site A warmed the
30s cache moments earlier, that current() call is a cache HIT with zero second
on-chain fetch.

We assert the routing boundary by counting calls to bankroll_provider.current
itself (the seam Site B must use). The repo conftest autouse fixture
(_bankroll_provider_test_isolation) already stubs current() and forbids the live
_fetch_balance; each test below installs its own per-test current() counter that
overrides that default (function-scoped, applied last). This is the faithful
"no second on-chain fetch" assertion under the test harness: if Site B built a
fresh PolymarketClient instead of calling current(), the counter would stay 0
AND the live path would fire — exactly the RED state on pre-dedupe code.

The two other jobs of the gate are independently asserted:
  (2) fail-closed submit semantics when current() returns None (wallet unreachable):
      no synthetic bankroll is installed, and later submit/sizing consumers still
      fail closed via bankroll_provider.cached();
  (3) CollateralLedger global singleton install on every success path; and
  (4) the clob= test-injection path stays entirely independent of current().
"""

from datetime import datetime, timedelta, timezone

import pytest

import src.main as main_mod
from src.runtime import bankroll_provider
from src.state import collateral_ledger


@pytest.fixture(autouse=True)
def _reset_ledger_global():
    """Reset the CollateralLedger global before and after each test.

    (The conftest autouse fixture already resets bankroll_provider's cache and
    stubs current(); we add the ledger-global reset on top.)
    """
    bankroll_provider.reset_cache_for_tests()
    collateral_ledger.configure_global_ledger(None)
    yield
    collateral_ledger.configure_global_ledger(None)
    bankroll_provider.reset_cache_for_tests()


def _record(value=123.45):
    return bankroll_provider.BankrollOfRecord(
        value_usd=value,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        source="polymarket_wallet",
        authority="canonical",
        staleness_seconds=0.0,
        cached=True,  # warm cache hit — Site A already fetched
    )


def _install_trade_db_snapshot(*, value_usd: float, age_seconds: float = 0.0):
    """Write a CHAIN collateral snapshot into the patched trade DB path.

    _startup_wallet_check installs a new path-backed CollateralLedger, so this
    test has to persist the snapshot instead of configuring an in-memory global.
    """
    from src.state import db as state_db

    ledger = collateral_ledger.CollateralLedger(db_path=state_db._zeus_trade_db_path())
    ledger.set_snapshot(
        collateral_ledger.CollateralSnapshot(
            pusd_balance_micro=int(value_usd * 1_000_000),
            pusd_allowance_micro=int(value_usd * 1_000_000),
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
            authority_tier="CHAIN",
        )
    )


class _CurrentCounter:
    """Counts calls to bankroll_provider.current; returns a warm record."""

    def __init__(self, value=123.45, returns_none=False):
        self.calls = 0
        self._value = value
        self._returns_none = returns_none

    def __call__(self, **_kwargs):
        self.calls += 1
        return None if self._returns_none else _record(self._value)


class _FakeClob:
    """Injected clob — get_balance is the ONLY thing the test path may touch."""

    def __init__(self, balance=77.0):
        self._balance = balance

    def get_balance(self):
        return self._balance


def test_site_b_routes_through_current_no_second_fetch(monkeypatch):
    """PRIMARY INVARIANT: production gate (clob=None) routes through
    bankroll_provider.current() (the warm-cache seam) — exactly ONE current()
    call, NO fresh PolymarketClient — AND installs the CollateralLedger global.

    On pre-dedupe code Site B builds its own PolymarketClient + get_balance(),
    so current() is never called → counter stays 0 → this test goes RED.
    """
    counter = _CurrentCounter(value=123.45)
    monkeypatch.setattr(bankroll_provider, "current", counter)

    main_mod._startup_wallet_check(clob=None)

    assert counter.calls == 1, (
        f"Site B did NOT route through bankroll_provider.current() "
        f"(calls={counter.calls}); it issued its own wallet fetch instead of "
        "reusing the warm Site A cache."
    )
    # Job (2): CollateralLedger singleton installed.
    assert collateral_ledger.get_global_ledger() is not None, (
        "CollateralLedger global singleton was not installed by _startup_wallet_check"
    )


def test_submit_fail_closed_but_daemon_continues_when_current_returns_none(monkeypatch):
    """FAIL-CLOSED PRESERVED: current() returns None (wallet unreachable / never
    warmed) → _startup_wallet_check(clob=None) must not synthesize bankroll or
    crash monitoring. Submit/sizing remains blocked downstream by the empty
    bankroll_provider cache."""
    counter = _CurrentCounter(returns_none=True)
    monkeypatch.setattr(bankroll_provider, "current", counter)

    main_mod._startup_wallet_check(clob=None)
    assert counter.calls == 1, "fail-closed path must still consult current()"
    assert bankroll_provider.cached() is None


def test_current_none_warms_from_fresh_collateral_snapshot(monkeypatch):
    """BOOT RECOVERY: wallet current() may be unavailable while the capital
    sidecar has already persisted a fresh CHAIN snapshot. Startup must consume
    that durable truth immediately, before boot recovery/allocator refresh runs.
    """
    _install_trade_db_snapshot(value_usd=1098.62)
    counter = _CurrentCounter(returns_none=True)
    monkeypatch.setattr(bankroll_provider, "current", counter)

    main_mod._startup_wallet_check(clob=None)

    assert counter.calls == 1, "startup still tries the bankroll provider first"
    record = bankroll_provider.cached()
    assert record is not None
    assert record.value_usd == 1098.62
    assert record.source == "collateral_ledger_snapshot"


def test_current_none_does_not_warm_from_stale_collateral_snapshot(monkeypatch):
    """Freshness remains fail-closed: startup may use the sidecar snapshot only
    while it is still inside the collateral-ledger freshness bound.
    """
    _install_trade_db_snapshot(value_usd=1098.62, age_seconds=3600.0)
    counter = _CurrentCounter(returns_none=True)
    monkeypatch.setattr(bankroll_provider, "current", counter)

    main_mod._startup_wallet_check(clob=None)

    assert counter.calls == 1
    assert bankroll_provider.cached() is None


def test_injected_clob_does_not_touch_bankroll_provider(monkeypatch):
    """TEST-INJECTION UNTOUCHED: clob=<fake> uses the fake's get_balance and never
    calls bankroll_provider.current (counter stays 0)."""
    counter = _CurrentCounter(value=999.0)
    monkeypatch.setattr(bankroll_provider, "current", counter)

    main_mod._startup_wallet_check(clob=_FakeClob(balance=77.0))

    assert counter.calls == 0, (
        f"Injected-clob path touched bankroll_provider.current (calls={counter.calls}); "
        "it must use the injected clob exclusively."
    )
    # Job (2) still preserved on the injected path.
    assert collateral_ledger.get_global_ledger() is not None
